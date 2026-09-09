from spider_bench.config import load_country_config


def test_load_poland():
    cfg = load_country_config("configs/poland.yaml")
    assert cfg.aws.bucket == "spiders-dataset-088543363904"
    assert cfg.country_prefix == "poland"
