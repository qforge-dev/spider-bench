# Final benchmark data sources

All **2,183 photos in species-id-v5**, including the **2,000 used in the published comparison**, come from iNaturalist. The taxon pool derives from a Polish spider checklist; the photos themselves were taken worldwide. The final suite covers 671 species and subspecies.

## Photo and label evidence

Each task preserves the source observation URL, observation and photo IDs, observer ID, source taxon ID, photo attribution, and license. It also links to a frozen observation response and the downloaded source image, with SHA-256 hashes for both. The prepared JPEG has its own hash and public dataset URL.

The source observation's scientific name had to match the expected label exactly, its taxon rank had to be species or subspecies, and its identification had to be research grade. The selected photo had to belong to the observation. Captive observations and photos without an accepted license were excluded.

These records are available in every run's `tasks.jsonl`; for example, the [Gemini high task snapshot](../data/benchmarks/runs/gemini-20260910-230024/tasks.jsonl) contains the same 2,000 tasks used by all 16 published runs. The underlying source responses and original images, including records excluded during preparation, are preserved. See the [dataset commands](../src/spider_bench/README.md#dataset-commands).

## What is included in the results

The full suite was prepared from a frozen pool of 2,655 source records. Its 472 exclusions and image-processing rules are documented in the [benchmark methodology](benchmark-v5.md#images-and-labels). Unreviewed Wikimedia search results were excluded; a searched species name was not accepted as evidence for the label of a returned image. GBIF and Wikimedia are not photo sources in the final v5 task set.

Taxonomic labels remain community identifications and can be wrong. The dataset checks establish source provenance, rather than guaranteeing that every species can be identified from the available photograph. Public availability also does not establish that the images were absent from model training.

See [licensing](licensing.md) for the license counts and attribution requirements for these photos.
