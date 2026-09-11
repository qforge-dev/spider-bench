# Photo licenses and attribution

The repository's code is licensed under [MIT](../LICENSE). Photos and third-party source records retain their original terms; the MIT license does not replace them.

The final benchmark retains the license and attribution recorded for each iNaturalist photo. The frozen task files contain these license counts:

| Photo license | Published 2,000-photo comparison | Full 2,183-photo suite |
| :--- | ---: | ---: |
| [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) | 1,586 | 1,739 |
| [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | 377 | 405 |
| [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | 37 | 39 |
| **Total** | **2,000** | **2,183** |

The task's `meta.license`, `meta.attribution`, and `meta.source_url` identify the recorded terms, photo credit, and original observation. Source observation responses and original images are preserved with checksums, so the benchmark's provenance does not depend on a live page remaining unchanged. See the [dataset commands](../src/spider_bench/README.md#dataset-commands).

CC BY and CC BY-NC photos require attribution, a license link, and an indication of changes when reused. CC BY-NC also restricts commercial use. The repository does not replace these per-photo terms with a single unrestricted image license. Refer to the linked licenses and each photo's source record when redistributing images.

The benchmark's prepared copies apply EXIF orientation, remove metadata, resize within the documented limits, and encode as JPEG. Preserve the source credit and identify these changes when reusing a prepared copy. The detailed [image-processing policy](benchmark-v5.md#images-and-labels) describes those transformations.
