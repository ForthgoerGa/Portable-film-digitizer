from models import FilmType, PipelineParams, QualityResult


def test_film_type_values():
    assert FilmType.NEGATIVE_35MM.value == "negative_35mm"
    assert FilmType.NEGATIVE_120.value == "negative_120"
    assert FilmType.POSITIVE_35MM.value == "positive_35mm"
    assert FilmType.POSITIVE_120.value == "positive_120"


def test_pipeline_params_defaults():
    params = PipelineParams()
    assert params.wb_clip_percent == 0.5
    assert params.black_point == 0.8
    assert params.white_point == 99.2
    assert params.clahe_clip == 1.4
    assert params.unsharp_amount == 0.35
    assert params.desat_strength == 0.20
    assert params.desat_sigma == 7.0
    assert params.shadow_lift == 0.0


def test_pipeline_params_custom():
    params = PipelineParams(clahe_clip=2.0, shadow_lift=15.0)
    assert params.clahe_clip == 2.0
    assert params.shadow_lift == 15.0
    assert params.wb_clip_percent == 0.5


def test_quality_result_construction():
    params = PipelineParams(clahe_clip=1.8)
    result = QualityResult(score=0.82, passed=True, feedback="Good exposure", suggested_params=params)
    assert result.score == 0.82
    assert result.passed is True
    assert result.suggested_params.clahe_clip == 1.8

