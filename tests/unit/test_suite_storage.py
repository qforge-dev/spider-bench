import base64
import io
import json

import pytest
from botocore.exceptions import ClientError
from PIL import Image

from spider_bench.benchmark.prepare import validate_suite
from spider_bench.benchmark.protocol import mixed_candidates, prepare_image
from spider_bench.benchmark.suite_storage import (
    build_publication, cached_object, digest, json_bytes, publish_suite, restore_suite,
)
from spider_bench.benchmark.tasks import _prompts, tasks_hash


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.writes = []
        self.fail_key = None

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key, **kwargs):
        data = self.get_object(Bucket, Key)['Body'].read()
        return {'ContentLength': len(data),
                'ChecksumSHA256': base64.b64encode(bytes.fromhex(digest(data))).decode()}

    def put_object(self, Bucket, Key, Body, IfNoneMatch, ChecksumSHA256, **kwargs):
        if Key == self.fail_key:
            raise RuntimeError('interrupted upload')
        assert IfNoneMatch == '*'
        assert Key not in self.objects
        assert ChecksumSHA256 == base64.b64encode(bytes.fromhex(digest(Body))).decode()
        self.objects[Key] = Body
        self.writes.append(Key)
        return {'ChecksumSHA256': ChecksumSHA256}


@pytest.fixture
def publication(tmp_path):
    directory = tmp_path / 'prepared' / 'species-id-v5'
    cache = tmp_path / 'preparation'
    directory.mkdir(parents=True)
    (cache / 'images').mkdir(parents=True)
    (cache / 'observations').mkdir()
    rows = []
    taxa = {'Aa a': 'A', 'Bb b': 'B'}
    for index, (taxon, family) in enumerate(taxa.items(), 1):
        photo_id = index + 10
        raw = io.BytesIO()
        Image.new('RGB', (640, 480), 'green' if index == 1 else 'red').save(raw, 'PNG')
        data, metadata = prepare_image(raw.getvalue())
        image_path = cache / 'images' / f"{metadata['sha256']}.jpg"
        image_path.write_bytes(data)
        (cache / f'images/inat-{photo_id}.source').write_bytes(raw.getvalue())
        observation = {'id': index, 'quality_grade': 'research', 'captive': False,
                       'taxon': {'name': taxon, 'rank': 'species'},
                       'photos': [{'id': photo_id, 'license_code': 'cc-by'}]}
        source_path = cache / f'observations/{index}.json'
        source_path.write_bytes(json_bytes(observation))
        candidates = mixed_candidates(taxon, taxa, metadata['sha256'])
        system, user = _prompts(candidates)
        rows.append({'task_id': f'species-id-v5:row{index}', 'correct_taxon': taxon,
                     'candidates': candidates, 'image_sha256': metadata['sha256'],
                     'image_local_path': str(image_path), 'image_public_url': '',
                     'system_prompt': system, 'user_prompt': user,
                     'meta': {'family': family, 'image': metadata,
                              'observation_id': str(index), 'photo_id': str(photo_id),
                              'source_snapshot': str(source_path),
                              'source_snapshot_sha256': digest(source_path.read_bytes()),
                              'source_image_sha256': digest(raw.getvalue())}})
    # An excluded source record must be archived too.
    (cache / 'observations/3.json').write_text('{"id": 3}')
    source_tasks = tmp_path / 'source-tasks.jsonl'
    source_tasks.write_text(json.dumps({'task_id': 'original'}) + '\n')
    (directory / 'tasks.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (directory / 'exclusions.jsonl').write_text('{"observation_id":"3","reason":"test"}\n')
    manifest = {'suite': 'species-id-v5', 'protocol_version': 5, 'seed': 42,
                'tasks': 2, 'taxa': 2, 'tasks_hash': tasks_hash(rows),
                'source_tasks': str(source_tasks), 'source_tasks_hash': tasks_hash([{'task_id': 'original'}])}
    (directory / 'manifest.json').write_bytes(json_bytes(manifest))
    plan = build_publication(directory, bucket='bucket', prefix='poland/', region='us-east-1',
                             preparation_cache=cache, staging=tmp_path / 'staging')
    return directory, cache, plan, rows[0]


def test_publish_and_restore_without_original_local_files(publication, tmp_path):
    directory, preparation, plan, original = publication
    s3 = FakeS3()
    result = publish_suite(plan, bucket='bucket', s3=s3)
    assert result['files'] == len(s3.objects)
    assert s3.writes[-1] == 'poland/benchmarks/species-id-v5/COMPLETE'
    portable = json.loads(s3.objects['poland/benchmarks/species-id-v5/tasks.jsonl'].splitlines()[0])
    assert 'image_local_path' not in portable and 'source_snapshot' not in portable['meta']
    assert str(tmp_path).encode() not in s3.objects['poland/benchmarks/species-id-v5/tasks.jsonl']
    for field in ('task_id', 'candidates', 'correct_taxon', 'image_sha256', 'system_prompt', 'user_prompt'):
        assert portable[field] == original[field]
    preparation.rename(tmp_path / 'unavailable')
    restored = tmp_path / 'another-machine' / 'suite'
    cache = tmp_path / 'new-cache'
    restore_suite(restored, suite='species-id-v5', bucket='bucket', prefix='poland/',
                  s3=s3, cache=cache, archive=True)
    assert validate_suite(restored, cache=cache, download=False)['valid']
    for item in plan['inventory']['files'].values():
        assert digest((cache / item['sha256'][:2] / item['sha256']).read_bytes()) == item['sha256']
    assert (directory / 'tasks.jsonl').exists()  # publishing preserves preparation
    assert publish_suite(plan, bucket='bucket', s3=s3)['already_published']


def test_interrupted_upload_cannot_be_restored_and_can_resume(publication, tmp_path):
    _, _, plan, _ = publication
    s3 = FakeS3()
    s3.fail_key = plan['prefix'] + 'source-tasks.jsonl'
    with pytest.raises(RuntimeError, match='interrupted'):
        publish_suite(plan, bucket='bucket', s3=s3)
    assert plan['prefix'] + 'COMPLETE' not in s3.objects
    with pytest.raises(ClientError):
        restore_suite(tmp_path / 'restore', suite='species-id-v5', bucket='bucket', prefix='poland/', s3=s3)
    s3.fail_key = None
    publish_suite(plan, bucket='bucket', s3=s3)
    assert plan['prefix'] + 'COMPLETE' in s3.objects
    modified = {**plan, 'complete': {**plan['complete'], 'tasks_hash': 'changed'}}
    with pytest.raises(ValueError, match='different completed suite'):
        publish_suite(modified, bucket='bucket', s3=s3)


def test_caches_repair_corruption_but_reject_bad_s3_bytes(tmp_path):
    s3 = FakeS3()
    data = b'prepared image'
    sha = digest(data)
    s3.objects['image'] = data
    assert cached_object('s3://bucket/image', sha, cache=tmp_path, s3=s3) == data
    target = tmp_path / sha[:2] / sha
    target.write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='missing or corrupt'):
        cached_object('s3://bucket/image', sha, cache=tmp_path, download=False)
    assert cached_object('s3://bucket/image', sha, cache=tmp_path, s3=s3) == data
    target.unlink()
    s3.objects['image'] = b'wrong bytes'
    with pytest.raises(ValueError, match='S3 asset checksum mismatch'):
        cached_object('s3://bucket/image', sha, cache=tmp_path, s3=s3)
    assert not target.exists()


def test_modified_inventory_and_local_suite_are_rejected(publication, tmp_path):
    _, _, plan, _ = publication
    s3 = FakeS3()
    publish_suite(plan, bucket='bucket', s3=s3)
    key = plan['prefix'] + 'inventory.json'
    inventory = s3.objects[key]
    s3.objects[key] += b' '
    directory = tmp_path / 'restore'
    with pytest.raises(ValueError, match='inventory checksum'):
        restore_suite(directory, suite='species-id-v5', bucket='bucket', prefix='poland/', s3=s3)
    assert not directory.exists()
    s3.objects[key] = inventory
    directory.mkdir()
    (directory / 'tasks.jsonl').write_text('different task selection')
    with pytest.raises(ValueError, match='local suite differs'):
        restore_suite(directory, suite='species-id-v5', bucket='bucket', prefix='poland/',
                      s3=s3, cache=tmp_path / 'cache')


def test_existing_different_object_is_never_overwritten(publication):
    _, _, plan, _ = publication
    s3 = FakeS3()
    image = next(v for v in plan['artifacts'].values() if '/media/sha256/' in v['key'])
    s3.objects[image['key']] = b'pre-existing object'
    with pytest.raises(ValueError, match='refusing to overwrite'):
        publish_suite(plan, bucket='bucket', s3=s3)
    assert s3.objects[image['key']] == b'pre-existing object'
    assert plan['prefix'] + 'COMPLETE' not in s3.objects
