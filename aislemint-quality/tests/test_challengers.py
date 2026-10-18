from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from promo.challengers import SeasonalDemandModel, WeightedDemandModel, select_blend
from promo.model import exposures, clean_baseline_mask


@pytest.fixture
def training_panel(engine):
    frame=exposures(engine.panel[engine.panel.store_id=='367'])
    return frame[frame.date<=pd.Timestamp(engine.model.trained_until)].iloc[:2200]


@pytest.mark.parametrize('family',['hgb','xgboost','catboost','glm'])
def test_baseline_challengers_ignore_actual_sales_and_own_promotion_inputs(training_panel,family):
    if family in ['xgboost','catboost']:pytest.importorskip(family)
    clean=training_panel[clean_baseline_mask(training_panel)]
    model=SeasonalDemandModel(family,baseline=True,iterations=200 if family=='glm' else 8).fit(clean)
    frame=clean.tail(10).copy();before=model.predict(frame)
    frame['units']=1e9;frame['SPEND']=1e12;frame['discount']=.5
    frame['feature']=1;frame['display']=1
    for lag in range(1,5):frame[f'lag_dose_{lag}']=1
    np.testing.assert_allclose(model.predict(frame),before)
    assert np.isfinite(before).all() and (before>=0).all()
    assert 'discount' not in model.features and 'lag_dose_1' not in model.features
    assert 'season_sin_4' in model.features


@pytest.mark.parametrize('family',['hgb','xgboost','catboost'])
def test_response_challengers_preserve_current_discount_monotonicity(training_panel,family):
    if family in ['xgboost','catboost']:pytest.importorskip(family)
    model=SeasonalDemandModel(family,iterations=8).fit(training_panel)
    low=training_panel.tail(20).copy();high=low.copy()
    low['discount']=.1;high['discount']=.3
    assert (model.predict(high)>=model.predict(low)-1e-8).all()


def test_ensemble_weights_are_selected_from_calibration_and_can_improve_both_models():
    models={k:SimpleNamespace(features=['season_sin'],trained_until='2020-01-01') for k in ['a','b']}
    predictions={'a':np.array([5.,25.]),'b':np.array([15.,15.])}
    model,choice,rows=select_blend(models,predictions,np.array([10.,20.]))
    assert isinstance(model,WeightedDemandModel)
    assert choice['weights']=={'a':.5,'b':.5}
    assert choice['calibration_wape']==0
    assert all('test_wape' not in r for r in rows)


def test_invalid_ensemble_weights_are_rejected():
    with pytest.raises(ValueError):WeightedDemandModel([object(),object()],[-.1,1.1],['a','b'])


def test_calendar_features_change_with_date_without_reading_future_sales(training_panel):
    clean=training_panel[clean_baseline_mask(training_panel)]
    model=SeasonalDemandModel('hgb',baseline=True,iterations=5).fit(clean)
    a=clean.tail(1).copy();b=a.copy();b['date']=b.date+pd.Timedelta(weeks=3)
    assert model._encode(a)['season_sin_4'].iloc[0]!=model._encode(b)['season_sin_4'].iloc[0]
    b['units']=1e9
    c=b.copy();c['units']=0
    pd.testing.assert_frame_equal(model._encode(b),model._encode(c))
