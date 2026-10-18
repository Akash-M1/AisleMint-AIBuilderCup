from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
"""Versioned US retail occasions and federal observance, frozen into trained models."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
from dateutil.easter import easter, EASTER_ORTHODOX

from .runtime import ROOT
SOURCE=AssetPath('data/reference/us_holidays_2004_2021.csv')


def nth_monday(year,month,n):
    first=pd.Timestamp(year,month,1)
    return first+pd.Timedelta(days=(0-first.dayofweek)%7+7*(n-1))


def occasions(year):
    labor=nth_monday(year,9,1)
    may_end=pd.Timestamp(year,5,31)
    november=pd.Timestamp(year,11,1)
    thanksgiving=november+pd.Timedelta(days=(3-november.dayofweek)%7+21)
    return {
        '4th of July':[pd.Timestamp(year,7,4)],'Christmas Day':[pd.Timestamp(year,12,25)],
        'Christmas Eve':[pd.Timestamp(year,12,24)],'Columbus Day':[nth_monday(year,10,2)],
        'Eastern Easter':[pd.Timestamp(easter(year,method=EASTER_ORTHODOX))],
        'Juneteenth':[pd.Timestamp(year,6,19)],'Labor Day':[labor],
        'Labor Day Weekend':[labor-pd.Timedelta(days=2),labor-pd.Timedelta(days=1)],
        'Martin Luther King, Jr. Day':[nth_monday(year,1,3)],
        'Memorial Day':[may_end-pd.Timedelta(days=may_end.dayofweek)],
        "New Year's Day":[pd.Timestamp(year,1,1)],'New Year’s Eve':[pd.Timestamp(year,12,31)],
        'Thanksgiving Day':[thanksgiving],'Thanksgiving Eve':[thanksgiving-pd.Timedelta(days=1)],
        'Valentine’s Day':[pd.Timestamp(year,2,14)],'Veterans Day':[pd.Timestamp(year,11,11)],
        "Washington's Birthday":[nth_monday(year,2,3)],'Western Easter':[pd.Timestamp(easter(year))]}


class USCalendar:
    def __init__(self,source=SOURCE):
        from .storage import read_csv_reference
        raw=read_csv_reference(source);dates=pd.to_datetime(raw.Date,format='%Y-%m-%d',errors='raise')
        if raw.duplicated(['Date','Holiday']).any():raise ValueError('Duplicate US holiday entries.')
        mismatches=(dates.dt.year.ne(raw.Year)|dates.dt.month.ne(raw.Month)|dates.dt.day.ne(raw.Day)|dates.dt.day_name().ne(raw.WeekDay))
        known={(int(y),name):set(g.Date) for (y,name),g in raw.assign(Year=dates.dt.year).groupby(['Year','Holiday'])}
        self.events=[];rule_differences=[]
        for year in range(2004,2041):
            for name,expected in occasions(year).items():
                values=known.get((year,name));generated={str(d.date()) for d in expected}
                if values is not None and values!=generated:rule_differences.append(dict(year=year,name=name,supplied=sorted(values),rule=sorted(generated)))
                for date in sorted(values if values is not None else generated):
                    self.events.append(dict(date=date,name=name,kind='occasion',origin='supplied_csv' if values is not None else 'calendar_rule'))
        observed=USFederalHolidayCalendar().holidays(start='2004-01-01',end='2040-12-31',return_name=True)
        for dt,name in observed.items():
            self.events.append(dict(date=str(dt.date()),name=str(name),kind='federal_observed',origin='federal_rule'))
        self.events.sort(key=lambda e:(e['date'],e['kind'],e['name']))
        self.names=sorted(raw.Holiday.unique())
        self.metadata=dict(version='us-retail-federal-v1',region='US',source_name=managed(source).name,
            source_sha256=hashlib.sha256(managed(source).read_bytes()).hexdigest(),supplied_rows=len(raw),
            supplied_start=str(dates.min().date()),supplied_end=str(dates.max().date()),
            coverage_start='2004-01-01',coverage_end='2040-12-31',
            corrected_metadata_rows=int(mismatches.sum()),rule_differences=rule_differences,
            federal_reference='https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/',
            note='Actual retail occasions and federal observed dates are distinct. Juneteenth occasions before 2021 do not imply federal holiday status. Dates after 2021 are rule-generated, not supplied observations.')
        self.metadata['calendar_sha256']=hashlib.sha256(json.dumps(self.events,sort_keys=True).encode()).hexdigest()

    @lru_cache(maxsize=512)
    def period_features(self,dates,step):
        events=pd.DataFrame(self.events);event_dates=pd.to_datetime(events.date)
        rows=[]
        for end in pd.to_datetime(list(dates)):
            def in_period(offset=0):
                shifted=end+pd.Timedelta(days=offset*step)
                return (event_dates>shifted-pd.Timedelta(days=step))&(event_dates<=shifted)
            active=events[in_period()]
            row={'us_holiday_any':float(len(active)>0),'us_holiday_days':float(active.date.nunique()),
                 'us_holiday_before':float(in_period(1).any()),'us_holiday_after':float(in_period(-1).any()),
                 'us_federal_observed':float(active.kind.eq('federal_observed').any())}
            for name in self.names:
                row['us_'+re.sub('[^a-z0-9]+','_',name.lower()).strip('_')]=float(((active.name==name)&(active.kind=='occasion')).any())
            rows.append(row)
        return pd.DataFrame(rows,index=pd.DatetimeIndex(dates))

    def features(self,dates,step):
        values=pd.DatetimeIndex(dates)
        unique=tuple(str(d.date()) for d in values.unique())
        result=self.period_features(unique,step).reindex(values)
        result.index=dates.index
        return result

    def calendar(self,period_ends,step):
        result=[]
        for end in pd.to_datetime(list(period_ends)):
            start=end-pd.Timedelta(days=step)
            matches=[{**e,'period_end':str(end.date())} for e in self.events if str(start.date())<e['date']<=str(end.date())]
            result.extend(matches)
        return result


@lru_cache(maxsize=1)
def us_calendar():return USCalendar()

class ClientCalendar:
    """Client-declared local occasions; never transfer the US calendar to India."""
    def __init__(self,frame):
        self.dates=sorted(str(pd.Timestamp(d).date()) for d in frame.loc[frame.get('holiday',pd.Series(0,index=frame.index)).eq(1),'date'].unique())
        self.metadata=dict(version='client-local-v1',region='IN',source_name='Client historical holiday flags',coverage_start=str(pd.to_datetime(frame.date).min().date()),coverage_end=str(pd.to_datetime(frame.date).max().date()),note='Client-declared local holidays. Future regional festivals require supplied future dates; US calendar is disabled.')
    def features(self,dates,step):
        flags=[any(str((pd.Timestamp(d)-pd.Timedelta(days=step)).date())<h<=str(pd.Timestamp(d).date()) for h in self.dates) for d in dates]
        return pd.DataFrame({'us_holiday_any':0.,'client_holiday_any':np.asarray(flags,dtype=float)},index=dates.index)
    def calendar(self,period_ends,step):
        return [dict(date=h,name='Client local holiday',kind='occasion',origin='client',period_end=str(pd.Timestamp(d).date())) for d in period_ends for h in self.dates if str((pd.Timestamp(d)-pd.Timedelta(days=step)).date())<h<=str(pd.Timestamp(d).date())]

def calendar_for(frame):
    from .data import market_metadata
    return ClientCalendar(frame) if market_metadata(frame)['country']=='IN' else us_calendar()
