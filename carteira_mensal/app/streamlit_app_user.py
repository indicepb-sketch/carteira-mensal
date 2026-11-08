from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import re
import sys
from typing import Any
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
try:
    import plotly.graph_objects as go
except Exception:
    go=None
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from production_registry import load_active, load_methodology, load_methodology_display_name
from portfolio_execution import (
    executable_portfolio as build_executable_portfolio,
    is_cdi,
    norm,
    practical_partial_return as calculate_practical_partial_return,
)
EXCEL_DIR=ROOT/'output'/'excel'
CDI_MONTHLY_PATH=ROOT/'data'/'processed'/'cdi_mensal_ipeadata.csv'
IBOV_MONTHLY_PATH=ROOT/'data'/'processed'/'ibov_mensal_oficial.csv'
SCENARIO='TOP15'; SCENARIO_LABEL='Top 15 operacional'; PLATFORM_MAX_STOCKS=15; HISTORY_CAPITAL=10000.0
SOURCE_CDI_IR=0.225; PLATFORM_CDI_IR=0.225
st.set_page_config(page_title='Carteira Mensal', page_icon='CM', layout='wide', initial_sidebar_state='expanded')
@dataclass(frozen=True)
class AppFiles:
    forward:Path|None; partial:Path|None; operational:Path|None; version:str; display_name:str|None=None
def file_sort_key(path:Path):
    month_match=re.search(r'(20\d{2})_(\d{2})', path.stem)
    month_key=f"{month_match.group(1)}{month_match.group(2)}" if month_match else "000000"
    version_match=re.search(r'v(\d{8}_\d{6})', path.stem)
    version_key=version_match.group(1) if version_match else "00000000_000000"
    try:
        mtime=path.stat().st_mtime
    except Exception:
        mtime=0
    return (month_key, version_key, mtime, path.name)
def latest(pattern:str)->Path|None:
    files=sorted(EXCEL_DIR.glob(pattern), key=file_sort_key); return files[-1] if files else None
def latest_official_partial(month:str,forward_name:str,version:str)->Path|None:
    candidates=sorted(EXCEL_DIR.glob(f'parcial_carteira_forward_{month.replace("-","_")}_v*.xlsx'),key=file_sort_key,reverse=True)
    for path in candidates:
        try:
            frame=pd.read_excel(path,sheet_name='Resumo Parcial')
            values=dict(zip(frame['metrica'].astype(str),frame['valor']))
            result=fnum(values.get('retorno_modelo_executavel'))
            if (values.get('calculo_oficial')=='executavel_top15_capital_10000'
                and values.get('arquivo_forward_usado')==forward_name
                and values.get('versao_modelo')==version
                and str(values.get('data_corte_comum',''))[:10]==str(values.get('data_avaliacao_parcial',''))[:10]
                and str(values.get('status','')) in {'parcial_mes_em_andamento','fechamento_mes'}
                and not np.isnan(result)):
                return path
        except (OSError,ValueError,KeyError):
            continue
    return None
def files()->AppFiles:
    forward,manifest=load_active(ROOT)
    execution=load_methodology(manifest['version'],ROOT)['execution']
    if (execution['scenario']!=SCENARIO or execution['max_stocks']!=PLATFORM_MAX_STOCKS
        or execution['ranking']!=['nota_final_desc','peso_recomendado_desc']
        or execution['min_weight_default']!=0.01 or execution['fractional_default'] is not True
        or execution['capital_reference']!=HISTORY_CAPITAL
        or execution['quantity_rule']!='floor_to_whole_shares_or_lots_of_100'
        or execution['unallocated_to']!='CDI'):
        raise ValueError('Regras de execucao divergem da metodologia versionada')
    partial_path=latest_official_partial(manifest['month'],forward.name,manifest['version'])
    return AppFiles(forward, partial_path, latest('historico_v2_top15_capital.xlsx') or latest('shadow_teste49_top15_regime_capital.xlsx') or latest('shadow_teste46_carteira_executavel.xlsx') or latest('shadow_teste45_consolidacao_final_t44a.xlsx'),manifest['version'],load_methodology_display_name(manifest['version'],ROOT))
@st.cache_data(show_spinner=False)
def _sheet_cached(path:str,name:str,stamp:tuple)->pd.DataFrame:
    try: return pd.read_excel(path,sheet_name=name)
    except Exception: return pd.DataFrame()
def sheet(path:str,name:str)->pd.DataFrame:
    # a chave do cache inclui data e tamanho do arquivo: um arquivo regenerado com o mesmo nome (ex.: historico_v2) nao serve dados antigos
    try: st_=Path(path).stat(); stamp=(st_.st_mtime_ns,st_.st_size)
    except OSError: stamp=(0,0)
    return _sheet_cached(path,name,stamp)
@st.cache_data(show_spinner=False)
def calendar_cdi_monthly()->pd.DataFrame:
    if not CDI_MONTHLY_PATH.exists(): return pd.DataFrame()
    df=pd.read_csv(CDI_MONTHLY_PATH)
    if {'mes','cdi_bruto_mensal'}.issubset(df.columns):
        df['mes']=df['mes'].astype(str).str[:7]
        df['retorno_cdi_liquido_calendario']=pd.to_numeric(df['cdi_bruto_mensal'],errors='coerce')*(1-PLATFORM_CDI_IR)
        return df[['mes','retorno_cdi_liquido_calendario']]
    return pd.DataFrame()
@st.cache_data(show_spinner=False)
def calendar_ibov_monthly()->pd.DataFrame:
    if not IBOV_MONTHLY_PATH.exists(): return pd.DataFrame()
    df=pd.read_csv(IBOV_MONTHLY_PATH)
    if {'mes','retorno_ibov_oficial'}.issubset(df.columns):
        df['mes']=df['mes'].astype(str).str[:7]
        df['retorno_ibov_oficial']=pd.to_numeric(df['retorno_ibov_oficial'],errors='coerce')
        return df[['mes','retorno_ibov_oficial']]
    return pd.DataFrame()
def fields(path:Path|None,name:str)->dict[str,Any]:
    if not path: return {}
    df=sheet(str(path),name)
    for k in ['campo','metrica','item']:
        if {k,'valor'}.issubset(df.columns): return dict(zip(df[k].astype(str),df['valor']))
    return {}
def fnum(x:Any,default=np.nan)->float:
    try:
        if x is None or pd.isna(x): return float(default)
        return float(x)
    except Exception: return float(default)
def pct(x:Any,d:int=2)->str:
    n=fnum(x); return '-' if np.isnan(n) else f'{n*100:.{d}f}%'.replace('.',',')
def money(x:Any)->str:
    n=fnum(x)
    return '-' if np.isnan(n) else f'R$ {n:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
def format_brl_amount(value:float)->str:
    return f'{value:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
def parse_brl_amount(text:str)->float:
    value=text.strip().removeprefix('R$').strip()
    if not re.fullmatch(r'(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?',value):
        raise ValueError('Informe um valor como 10.000,00.')
    amount=Decimal(value.replace('.','').replace(',','.'))
    if amount<1000:
        raise ValueError('O valor minimo e R$ 1.000,00.')
    return float(amount)
def update_simulated_capital()->None:
    try:
        st.session_state.simulated_capital=parse_brl_amount(st.session_state.simulated_capital_text)
        st.session_state.simulated_capital_error=''
    except ValueError as exc:
        st.session_state.simulated_capital_error=str(exc)
    st.session_state.simulated_capital_text=format_brl_amount(st.session_state.simulated_capital)
def step_simulated_capital(amount:float)->None:
    st.session_state.simulated_capital=round(max(1000.0,st.session_state.simulated_capital+amount),2)
    st.session_state.simulated_capital_text=format_brl_amount(st.session_state.simulated_capital)
    st.session_state.simulated_capital_error=''
def simulated_capital_input()->float:
    if 'simulated_capital' not in st.session_state:
        st.session_state.simulated_capital=10000.0
        st.session_state.simulated_capital_text=format_brl_amount(10000.0)
    with st.sidebar:
        st.markdown('Valor para simular')
        amount_col,minus_col,plus_col=st.columns([5,1,1],gap='small')
        with amount_col:
            st.text_input('Valor para simular',key='simulated_capital_text',label_visibility='collapsed',on_change=update_simulated_capital)
        with minus_col:
            st.button('-',key='decrease_capital',help='Reduzir R$ 500,00',use_container_width=True,disabled=st.session_state.simulated_capital<=1000.0,on_click=step_simulated_capital,args=(-500.0,))
        with plus_col:
            st.button('+',key='increase_capital',help='Aumentar R$ 500,00',use_container_width=True,on_click=step_simulated_capital,args=(500.0,))
        if st.session_state.get('simulated_capital_error'):
            st.error(st.session_state.simulated_capital_error)
    return st.session_state.simulated_capital
def first(d:dict[str,Any],keys:list[str])->Any:
    for k in keys:
        v=d.get(k)
        if v is not None and not pd.isna(v): return v
    return None
def platform_cdi_return(v:Any)->float:
    net=fnum(v)
    if np.isnan(net): return np.nan
    gross=net/(1-SOURCE_CDI_IR) if SOURCE_CDI_IR<1 else net
    return gross*(1-PLATFORM_CDI_IR)
def format_updated_at(value:Any,path:Path|None=None)->str:
    file_dt=None
    if path:
        try: file_dt=datetime.fromtimestamp(path.stat().st_mtime)
        except Exception: file_dt=None
    parsed=pd.to_datetime(value,errors='coerce') if value is not None else pd.NaT
    if pd.notna(parsed):
        if parsed.hour or parsed.minute or parsed.second: return parsed.strftime('%d/%m/%Y %H:%M')
        return parsed.strftime('%d/%m/%Y')
    return file_dt.strftime('%d/%m/%Y %H:%M') if file_dt else '-'
def upcoming_formation(value:Any,today=None)->bool:
    formation=pd.to_datetime(value,errors='coerce') if value is not None else pd.NaT
    local_today=today or datetime.now(ZoneInfo('America/Fortaleza')).date()
    return bool(pd.notna(formation) and formation.date()>local_today)
def portfolio(f:AppFiles)->pd.DataFrame:
    if not f.forward: return pd.DataFrame()
    df=sheet(str(f.forward),'Carteira Aplicada')
    if df.empty: df=sheet(str(f.forward),'Carteira Forward')
    if df.empty: return df
    df=norm(df)
    if 'ticker' in df: df['ticker']=df['ticker'].astype(str).str.upper()
    return df
def executable_portfolio(df:pd.DataFrame,capital:float,min_weight:float,fractional:bool):
    return build_executable_portfolio(df,capital,min_weight,fractional,PLATFORM_MAX_STOCKS)
def order_plan(df,capital,min_weight,fractional):
    ex,rem,_=executable_portfolio(df,capital,min_weight,fractional)
    v=pd.DataFrame({'Ativo':ex.get('ticker'),'Empresa':ex.get('nome'),'Setor':ex.get('setor'),'Peso final':ex.get('peso_executavel'),'Valor alvo':ex.get('valor_alvo'),'Preco':ex.get('preco_referencia'),'Quantidade':ex.get('quantidade'),'Valor estimado':ex.get('valor_estimado'),'Forma':ex.get('forma')})
    v['Valor alvo']=v['Valor alvo'].fillna(v['Valor estimado'])
    v['Peso final']=v['Peso final'].map(pct)
    for c in ['Valor alvo','Preco','Valor estimado']: v[c]=v[c].map(money)
    v['Quantidade']=v['Quantidade'].map(lambda x:'-' if pd.isna(x) else str(int(x)))
    return v,rem
def compound(s):
    vals=pd.to_numeric(s,errors='coerce').dropna(); return np.nan if vals.empty else float((1+vals).prod()-1)
def eq_month(s):
    vals=pd.to_numeric(s,errors='coerce').dropna(); return np.nan if vals.empty else float((1+compound(vals))**(1/len(vals))-1)
def historical_executable_portfolios(f):
    if not f.operational:
        df=pd.DataFrame()
    else:
        df=sheet(str(f.operational),'Carteiras Executaveis')
        if df.empty: df=sheet(str(f.operational),'Carteiras')
        if not df.empty and 'mes' in df.columns:
            if 'cenario' in df: df=df[df['cenario'].astype(str).eq(SCENARIO)].copy()
            if 'capital' in df:
                cap=pd.to_numeric(df['capital'],errors='coerce'); df=df[cap.sub(HISTORY_CAPITAL).abs().lt(.01)].copy()
            df=df.rename(columns={'tipo_linha':'tipo_alocacao','peso_final':'peso_executavel_total','contribuicao':'contribuicao_executavel'})
        else:
            df=pd.DataFrame()
    extra=all_finalized_partial_portfolio_rows(f)
    if not extra.empty:
        if df.empty or 'mes' not in df.columns:
            df=extra
        else:
            months=set(df['mes'].astype(str).str[:7])
            extra=extra[~extra['mes'].astype(str).str[:7].isin(months)]
            if not extra.empty: df=pd.concat([df,extra],ignore_index=True,sort=False)
    return df
def finalized_partial_files()->list[Path]:
    return sorted(EXCEL_DIR.glob('parcial_carteira_forward_2026_*.xlsx'), key=file_sort_key)
def finalized_partial_forward(ps:dict[str,Any],mes:str)->Path|None:
    name=str(ps.get('arquivo_forward_usado') or '').strip()
    if name:
        p=EXCEL_DIR/name
        if p.exists(): return p
    key=mes.replace('-','_')
    return latest(f'carteira_forward_{key}*.xlsx')
def finalized_partial_portfolio_rows(f,partial_path:Path|None=None,require_finalized:bool=True,capital:float=HISTORY_CAPITAL,min_weight:float=0.01,fractional:bool=True)->pd.DataFrame:
    partial_path=partial_path or f.partial
    if not partial_path: return pd.DataFrame()
    ps=fields(partial_path,'Resumo Parcial')
    if require_finalized and 'fechamento' not in str(ps.get('status','')).lower(): return pd.DataFrame()
    mes=str(ps.get('mes') or ps.get('mes_referencia') or '')[:7]
    if not mes: return pd.DataFrame()
    fp=finalized_partial_forward(ps,mes)
    if not fp: return pd.DataFrame()
    base=sheet(str(fp),'Carteira Aplicada')
    if base.empty: base=sheet(str(fp),'Carteira Forward')
    if base.empty: return pd.DataFrame()
    ex,_,_=executable_portfolio(base,capital,min_weight,fractional)
    if ex.empty: return pd.DataFrame()
    assets=sheet(str(partial_path),'Ativos')
    if not require_finalized and (assets.empty or not {'ticker','retorno_periodo'}.issubset(assets.columns)):
        return pd.DataFrame()
    out=ex.copy(); out['ticker']=out['ticker'].astype(str).str.upper()
    if not assets.empty and 'ticker' in assets:
        src=assets.copy(); src['ticker_key']=src['ticker'].astype(str).str.upper()
        out['ticker_key']=out['ticker'].astype(str).str.upper()
        cols=[c for c in ['ticker_key','retorno_periodo','preco_entrada','preco_atual','data_avaliacao'] if c in src.columns]
        out=out.merge(src[cols],on='ticker_key',how='left')
    cdi_ret=platform_cdi_return(first(ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo']))
    out['retorno_periodo']=pd.to_numeric(out.get('retorno_periodo',pd.Series(index=out.index)),errors='coerce')
    if not require_finalized and (out.loc[~out.apply(is_cdi,axis=1),'retorno_periodo'].isna().any() or pd.isna(cdi_ret)):
        return pd.DataFrame()
    out.loc[out.apply(is_cdi,axis=1),'retorno_periodo']=cdi_ret
    out['contribuicao_executavel']=pd.to_numeric(out.get('peso_executavel',pd.Series(index=out.index)),errors='coerce').fillna(0)*out['retorno_periodo'].fillna(0)
    out['mes']=mes
    out['tipo_alocacao']=out.apply(lambda r:'CDI' if is_cdi(r) else 'acoes',axis=1)
    out['peso_executavel_total']=out.get('peso_executavel')
    out['arquivo_origem']=partial_path.name
    cols=['mes','ticker','nome','setor','tipo_alocacao','peso_executavel_total','peso_modelo','quantidade','preco_referencia','preco_atual','valor_estimado','retorno_periodo','contribuicao_executavel','arquivo_origem']
    return out[[c for c in cols if c in out.columns]]
def all_finalized_partial_portfolio_rows(f)->pd.DataFrame:
    frames=[finalized_partial_portfolio_rows(f,p) for p in finalized_partial_files()]
    frames=[x for x in frames if not x.empty]
    return pd.concat(frames,ignore_index=True,sort=False) if frames else pd.DataFrame()
def finalized_partial_month_row(f,partial_path:Path|None=None,require_finalized:bool=True,capital:float=HISTORY_CAPITAL,min_weight:float=0.01,fractional:bool=True)->pd.DataFrame:
    partial_path=partial_path or f.partial
    if not partial_path: return pd.DataFrame()
    ps=fields(partial_path,'Resumo Parcial')
    status=str(ps.get('status','')).lower()
    if require_finalized and 'fechamento' not in status: return pd.DataFrame()
    mes=str(ps.get('mes') or ps.get('mes_referencia') or '')[:7]
    if not mes: return pd.DataFrame()
    rows=finalized_partial_portfolio_rows(f,partial_path,require_finalized,capital,min_weight,fractional)
    if rows.empty and not require_finalized: return pd.DataFrame()
    stock_value=float(pd.to_numeric(rows.loc[~rows.apply(is_cdi,axis=1),'valor_estimado'],errors='coerce').sum()) if not rows.empty and 'valor_estimado' in rows.columns else np.nan
    cdi_value=float(pd.to_numeric(rows.loc[rows.apply(is_cdi,axis=1),'valor_estimado'],errors='coerce').sum()) if not rows.empty and 'valor_estimado' in rows.columns else np.nan
    ret_pratico=np.nan
    if not rows.empty and 'contribuicao_executavel' in rows.columns:
        ret_pratico=float(pd.to_numeric(rows['contribuicao_executavel'],errors='coerce').fillna(0).sum())
    row={
        'mes':mes,
        'retorno_modelo':ret_pratico if not np.isnan(ret_pratico) else fnum(first(ps,['retorno_modelo_executavel','retorno_carteira_parcial_aplicada','retorno_carteira_periodo']),np.nan),
        'retorno_expost_ibov':fnum(first(ps,['retorno_ibov_parcial','retorno_ibov_periodo']),np.nan),
        'retorno_cdi_liquido_periodo':fnum(first(ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo']),np.nan),
        'peso_acoes_executavel':stock_value/capital if capital and not np.isnan(stock_value) else fnum(first(ps,['exposicao_acoes','peso_acoes']),np.nan),
        'peso_cdi_executavel':cdi_value/capital if capital and not np.isnan(cdi_value) else fnum(first(ps,['peso_defensivo_cdi','peso_cdi']),np.nan),
        'tipo_regime_expost':'fechamento_mes' if 'fechamento' in status else 'parcial_mes',
    }
    row['alfa']=row['retorno_modelo']-row['retorno_expost_ibov'] if not np.isnan(row['retorno_modelo']) and not np.isnan(row['retorno_expost_ibov']) else np.nan
    row['bateu_ibov']=bool(row['alfa']>0) if not np.isnan(row['alfa']) else False
    return pd.DataFrame([row])
def all_finalized_partial_month_rows(f)->pd.DataFrame:
    frames=[finalized_partial_month_row(f,p) for p in finalized_partial_files()]
    frames=[x for x in frames if not x.empty]
    if not frames: return pd.DataFrame()
    out=pd.concat(frames,ignore_index=True,sort=False)
    return out.drop_duplicates(subset=['mes'],keep='last') if 'mes' in out.columns else out
def monthly(f,current_capital:float=HISTORY_CAPITAL,current_min_weight:float=0.01,current_fractional:bool=True):
    if not f.operational: return pd.DataFrame()
    df=sheet(str(f.operational),'Mes a Mes')
    if df.empty: df=sheet(str(f.operational),'Mes a Mes vs 36C')
    if df.empty: return df
    if {'cenario','capital'}.issubset(df.columns):
        cap=pd.to_numeric(df['capital'],errors='coerce'); chosen=df[df['cenario'].astype(str).eq(SCENARIO)&cap.sub(HISTORY_CAPITAL).abs().lt(.01)]
        if not chosen.empty: df=chosen.copy()
    ren={'retorno':'retorno_modelo','retorno_executavel':'retorno_modelo','retorno_total':'retorno_modelo','retorno_ibov':'retorno_expost_ibov','alfa_executavel':'alfa','alfa_executavel_vs_ibov':'alfa','alfa_vs_ibov':'alfa','bateu_ibov_executavel':'bateu_ibov','peso_acoes':'peso_acoes_executavel','peso_cdi':'peso_cdi_executavel'}
    df=df.rename(columns={k:v for k,v in ren.items() if k in df.columns})
    if 'retorno_modelo' not in df.columns and 'retorno_total_operacional' in df.columns: df['retorno_modelo']=df['retorno_total_operacional']
    if 'alfa' not in df.columns and {'retorno_modelo','retorno_expost_ibov'}.issubset(df.columns): df['alfa']=pd.to_numeric(df['retorno_modelo'],errors='coerce')-pd.to_numeric(df['retorno_expost_ibov'],errors='coerce')
    if 'retorno_cdi_liquido_periodo' not in df.columns:
        rows=historical_executable_portfolios(f)
        if not rows.empty and {'mes','ticker','retorno_periodo'}.issubset(rows.columns):
            cdi=rows[rows['ticker'].astype(str).str.upper().eq('CDI')].copy(); cdi['mes']=cdi['mes'].astype(str).str[:7]
            df=df.merge(cdi[['mes','retorno_periodo']].rename(columns={'retorno_periodo':'retorno_cdi_liquido_periodo'}),on='mes',how='left')
    cdi_cal=calendar_cdi_monthly()
    if not cdi_cal.empty and 'mes' in df.columns:
        df['mes']=df['mes'].astype(str).str[:7]
        df=df.merge(cdi_cal,on='mes',how='left')
        df['retorno_cdi_liquido_periodo']=df['retorno_cdi_liquido_calendario'].combine_first(df.get('retorno_cdi_liquido_periodo'))
        df=df.drop(columns=['retorno_cdi_liquido_calendario'])
    extra=all_finalized_partial_month_rows(f)
    current_ps,_=partial(f)
    current=finalized_partial_month_row(f,f.partial,require_finalized=False,capital=current_capital,min_weight=current_min_weight,fractional=current_fractional) if current_ps else pd.DataFrame()
    if not current.empty:
        extra=pd.concat([extra,current],ignore_index=True,sort=False) if not extra.empty else current
    if not extra.empty and 'mes' in df.columns:
        extra=extra.drop_duplicates(subset=['mes'],keep='last')
        existing=set(df['mes'].astype(str).str[:7])
        extra=extra[~extra['mes'].astype(str).str[:7].isin(existing)]
        if not extra.empty:
            df=pd.concat([df,extra],ignore_index=True,sort=False)
    ibov_cal=calendar_ibov_monthly()
    if not ibov_cal.empty and 'mes' in df.columns:
        df['mes']=df['mes'].astype(str).str[:7]
        df=df.merge(ibov_cal,on='mes',how='left')
        df['retorno_expost_ibov']=df['retorno_ibov_oficial'].combine_first(df.get('retorno_expost_ibov'))
        df=df.drop(columns=['retorno_ibov_oficial'])
        if {'retorno_modelo','retorno_expost_ibov'}.issubset(df.columns):
            df['alfa']=pd.to_numeric(df['retorno_modelo'],errors='coerce')-pd.to_numeric(df['retorno_expost_ibov'],errors='coerce')
            df['bateu_ibov']=df['alfa'].gt(0)
    return df.sort_values('mes') if 'mes' in df.columns else df
def summary(f):
    if not f.operational: return {}
    cap=sheet(str(f.operational),'Resumo Capital')
    if not cap.empty and {'cenario','capital'}.issubset(cap.columns):
        cc=pd.to_numeric(cap['capital'],errors='coerce'); row=cap[cap['cenario'].astype(str).eq(SCENARIO)&cc.sub(HISTORY_CAPITAL).abs().lt(.01)]
        if not row.empty:
            r=row.iloc[0].to_dict(); return {'modelo':r.get('retorno_modelo'),'ibov':r.get('retorno_ibov'),'alfa':r.get('alfa_vs_ibov'),'acerto':r.get('taxa_acerto')}
    return {}
def current_forward_month(f)->str:
    ff=fields(f.forward,'Resumo Forward')
    return str(ff.get('mes_forward') or ff.get('mes_referencia') or '')[:7]
def month_label(mes:str)->str:
    names={1:'Janeiro',2:'Fevereiro',3:'Março',4:'Abril',5:'Maio',6:'Junho',7:'Julho',8:'Agosto',9:'Setembro',10:'Outubro',11:'Novembro',12:'Dezembro'}
    try:
        y,mo=str(mes)[:7].split('-')
        return f"{names.get(int(mo),mo)}/{y}"
    except Exception:
        return '-'
def portfolio_reference(f)->str:
    mes=current_forward_month(f)
    label=month_label(mes)
    return f"Carteira Recomendada - {label}" if label!='-' else 'Carteira Recomendada'
def partial(f):
    if not f.partial: return {},pd.DataFrame()
    ps=fields(f.partial,'Resumo Parcial')
    partial_month=str(ps.get('mes') or ps.get('mes_referencia') or '')[:7]
    forward_month=current_forward_month(f)
    if forward_month and partial_month and partial_month!=forward_month:
        return {},pd.DataFrame()
    return ps,sheet(str(f.partial),'Ativos')
def latest_previous_open_partial(f,history:pd.DataFrame)->tuple[Path|None,dict[str,Any]]:
    try:
        previous=str(pd.Period(current_forward_month(f),freq='M')-1)
    except (ValueError,TypeError):
        return None,{}
    if 'mes' in history and history['mes'].astype(str).str[:7].eq(previous).any():
        return None,{}
    manifest_path=ROOT/'output'/'production'/f'{previous}.json'
    try:
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return None,{}
    if not isinstance(manifest,dict) or manifest.get('month')!=previous:
        return None,{}
    forward_name=manifest.get('file'); version=manifest.get('version')
    if not forward_name or not version:
        return None,{}
    path=latest_official_partial(previous,forward_name,version)
    if not path:
        return None,{}
    details=fields(path,'Resumo Parcial')
    if str(details.get('status',''))!='parcial_mes_em_andamento' or str(details.get('mes',''))[:7]!=previous:
        return None,{}
    return path,details
def cumulative(df):
    if go is None or df.empty: return None
    fig=go.Figure()
    for col,label,color in [('retorno_modelo','Modelo','#2f80ed'),('retorno_expost_ibov','IBOV','#f97316'),('retorno_cdi_liquido_periodo','CDI liquido','#16a34a')]:
        if col in df.columns:
            vals=pd.to_numeric(df[col],errors='coerce').fillna(0); fig.add_trace(go.Scatter(x=df['mes'].astype(str),y=(1+vals).cumprod()-1,mode='lines+markers',name=label,line=dict(color=color)))
    fig.update_layout(height=360,yaxis_tickformat='.1%',margin=dict(l=10,r=10,t=30,b=10),legend=dict(orientation='h'),template='plotly_dark'); return fig
def allocation_chart(stocks,cdi):
    if go is None: return None
    fig=go.Figure(go.Pie(labels=['Acoes','CDI liquido'],values=[stocks,cdi],hole=.58,marker=dict(colors=['#2f80ed','#16a34a']))); fig.update_traces(textinfo='label+percent'); fig.update_layout(height=280,margin=dict(l=10,r=10,t=20,b=10),showlegend=False,template='plotly_dark'); return fig
def sector_exposure_chart(ex):
    if go is None or ex.empty or 'peso_executavel' not in ex.columns: return None
    d=ex.copy(); d['setor_exposicao']=d.apply(lambda r:'CDI / Reserva' if is_cdi(r) else str(r.get('setor') or 'Sem setor'),axis=1); d['peso_executavel']=pd.to_numeric(d['peso_executavel'],errors='coerce').fillna(0)
    g=d.groupby('setor_exposicao',as_index=False)['peso_executavel'].sum().sort_values('peso_executavel'); colors=g['setor_exposicao'].map(lambda x:'#16a34a' if x=='CDI / Reserva' else '#2f80ed')
    fig=go.Figure(go.Bar(x=g['peso_executavel'],y=g['setor_exposicao'],orientation='h',marker_color=colors,text=g['peso_executavel'].map(lambda v:f'{v:.1%}'),textposition='auto')); fig.update_layout(height=max(300,42*len(g)),xaxis_tickformat='.0%',template='plotly_dark',showlegend=False,margin=dict(l=10,r=10,t=20,b=20)); return fig
def historical_calendar_html(df, open_partial=None):
    months=[(1,'JAN'),(2,'FEV'),(3,'MAR'),(4,'ABR'),(5,'MAI'),(6,'JUN'),(7,'JUL'),(8,'AGO'),(9,'SET'),(10,'OUT'),(11,'NOV'),(12,'DEZ')]
    data=df.copy(); data['mes_dt']=pd.to_datetime(data.get('mes'),errors='coerce'); data=data.dropna(subset=['mes_dt'])
    if data.empty: return ''
    data['ano']=data['mes_dt'].dt.year; data['mes_num']=data['mes_dt'].dt.month
    for c in ['retorno_modelo','retorno_expost_ibov','alfa']: data[c]=pd.to_numeric(data.get(c),errors='coerce')
    rows={}
    partial_month=str(open_partial.get('mes',''))[:7] if open_partial else ''
    partial_date=pd.to_datetime(partial_month,format='%Y-%m',errors='coerce')
    years=set(data['ano'].astype(int).unique())
    if pd.notna(partial_date): years.add(partial_date.year)
    for year in sorted(years,reverse=True):
        ydf=data[data['ano'].eq(year)].copy(); cells=[]
        for mn,label in months:
            row=ydf[ydf['mes_num'].eq(mn)]
            if row.empty and pd.notna(partial_date) and year==partial_date.year and mn==partial_date.month:
                ret=fnum(open_partial.get('retorno_modelo_executavel'))
                ibov=fnum(first(open_partial,['retorno_ibov_parcial','retorno_ibov_periodo']))
                alfa=ret-ibov if not np.isnan(ret) and not np.isnan(ibov) else np.nan
                if not np.isnan(ret):
                    rc='pos' if ret>=0 else 'neg'; ic='pos' if not np.isnan(ibov) and ibov>=0 else 'neg'; ac='beat' if not np.isnan(alfa) and alfa>=0 else 'miss'
                    cutoff=format_updated_at(first(open_partial,['data_corte_comum','data_avaliacao_parcial']))
                    ibov_txt='-' if np.isnan(ibov) else f'IBOV {ibov*100:+.2f}%'
                    alfa_txt='-' if np.isnan(alfa) else f'{alfa*100:+.2f} p.p.'
                    cells.append(f'<td data-label="{label}" class="hist-cell partial"><div class="hist-partial-label">PARCIAL</div><div class="hist-ret {rc}">{ret*100:.2f}%</div><div class="hist-ibov {ic}">{ibov_txt}</div><div class="hist-sub {ac}">{alfa_txt}</div><div class="hist-partial-date">até {cutoff}</div></td>')
                    continue
            if row.empty: cells.append(f'<td data-label="{label}" class="empty">-</td>'); continue
            r=row.iloc[0]; ret=fnum(r.get('retorno_modelo')); ibov=fnum(r.get('retorno_expost_ibov')); alfa=fnum(r.get('alfa')); rc='pos' if not np.isnan(ret) and ret>=0 else 'neg'; ic='pos' if not np.isnan(ibov) and ibov>=0 else 'neg'; ac='beat' if not np.isnan(alfa) and alfa>=0 else 'miss' if not np.isnan(alfa) else 'neutral'
            ibov_txt='-' if np.isnan(ibov) else f'IBOV {ibov*100:+.2f}%'
            cells.append(f'<td data-label="{label}" class="hist-cell"><div class="hist-ret {rc}">{ret*100:.2f}%</div><div class="hist-ibov {ic}">{ibov_txt}</div><div class="hist-sub {ac}">{alfa*100:+.2f} p.p.</div></td>')
        tm=compound(ydf.get('retorno_modelo',pd.Series(dtype=float))); ti=compound(ydf.get('retorno_expost_ibov',pd.Series(dtype=float))); ta=tm-ti if not np.isnan(tm) and not np.isnan(ti) else np.nan; rc='pos' if not np.isnan(tm) and tm>=0 else 'neg'; ic='pos' if not np.isnan(ti) and ti>=0 else 'neg'; ac='beat' if not np.isnan(ta) and ta>=0 else 'miss'
        ti_txt='-' if np.isnan(ti) else f'IBOV {ti*100:+.2f}%'
        total=f'<td data-label="TOTAL" class="total hist-cell"><div class="hist-ret {rc}">{tm*100:.2f}%</div><div class="hist-ibov {ic}">{ti_txt}</div><div class="hist-sub {ac}">{ta*100:+.2f} p.p.</div></td>'
        rows[year]=(cells,total)
    html=['<div class="hist-card"><div class="hist-title">Rentabilidade Histórica <span class="hist-legend">retorno do modelo · IBOV no mês · diferença vs IBOV em p.p.</span></div><table class="hist-table"><thead><tr><th>ANO</th>']
    html.extend(f'<th>{label}</th>' for _,label in months)
    html.append('<th>TOTAL</th>')
    html.append('</tr></thead><tbody>')
    for year,(cells,total) in rows.items():
        html.append(f'<tr><th scope="row">{year}</th>')
        html.extend(cells)
        html.append(total)
        html.append('</tr>')
    html.append('</tbody></table></div>'); return ''.join(html)
def autorefresh(enabled: bool, minutes: int):
    if enabled:
        components.html(f"<script>setTimeout(function(){{window.parent.location.reload();}}, {minutes * 60000});</script>", height=0)
def css():
    st.markdown('''<style>
    .block-container{padding-top:1.4rem;max-width:1400px}
    .note{color:#aab3b9;line-height:1.5}
    .pill{display:inline-block;padding:4px 9px;border-radius:4px;background:#20312d;color:#d4f4e8;border:1px solid #35594e;font-size:.85rem}
    .hist-card{background:#171c21;border:1px solid #343d42;border-radius:8px;margin:18px 0;overflow-x:auto;scrollbar-color:#6b7b80 #20272b}
    .hist-card::-webkit-scrollbar{height:12px}
    .hist-card::-webkit-scrollbar-track{background:#20272b}
    .hist-card::-webkit-scrollbar-thumb{background:#6b7b80;border-radius:6px}
    .hist-title{font-weight:700;font-size:1.05rem;padding:16px 20px;border-bottom:1px solid #343d42}
    .hist-table{min-width:940px;width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0;font-size:.8rem}
    .hist-table th{background:#20272b;color:#e5ecea;text-align:center;padding:10px 3px;border-bottom:1px solid #343d42}
    .hist-table td{background:#171c21;text-align:center;padding:10px 3px;border-top:1px solid #30393e;overflow-wrap:anywhere}
    .hist-table th:first-child,.hist-table tbody th{position:sticky;left:0;width:54px;z-index:3;background:#20272b}
    .hist-table .total{background:#20312d}.hist-ret{font-weight:700}
    .hist-table td.partial{background:#1b2926;border:1px solid #3a7663}
    .hist-legend{font-size:.72rem;font-weight:400;color:#aab3b9;margin-left:10px}
    .hist-partial-label{color:#8be1bd;font-size:.7rem;font-weight:700}
    .hist-partial-date{color:#b7c1c0;font-size:.72rem;margin-top:5px}
    .hist-ret.pos{color:#7bd8a9}.hist-ret.neg{color:#f0918f}
    .hist-ibov{font-size:.7rem;color:#b7c1c0;margin-top:4px}
    .hist-ibov.pos{color:#90c4db}.hist-ibov.neg{color:#f5b0a7}
    .hist-sub{font-size:.7rem;color:#b7c1c0;margin-top:4px;min-height:17px}
    .hist-sub.beat{color:#7bd8a9;font-weight:700}.hist-sub.miss{color:#f5b0a7;font-weight:700}
    .hist-table .empty{color:#8c9998;font-weight:700}
    @media(max-width:700px){
      .hist-table{min-width:0}
      .hist-table thead{display:none}
      .hist-table tbody tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}
      .hist-table tbody th{grid-column:1/-1;text-align:left;position:static;width:auto}
      .hist-table td{min-width:0;min-height:105px;border-right:1px solid #30393e}
      .hist-table td::before{content:attr(data-label);display:block;color:#b7c1c0;font-size:.73rem;font-weight:700;margin-bottom:5px}
    }
    div[data-testid="stMetric"]{background:#1b2225;border:1px solid #34423f;border-radius:6px;padding:14px 16px;min-height:104px}
    div[data-testid="stMetric"] label,div[data-testid="stMetric"] [data-testid="stMetricValue"]{color:#f4f7f5!important;overflow-wrap:anywhere}
    div[data-testid="stMetric"] [data-testid="stMetricValue"]{font-size:1.8rem}
    </style>''',unsafe_allow_html=True)
def render_start(f,port,capital,min_w,frac):
    ff=fields(f.forward,'Resumo Forward'); ps,assets=partial(f)
    ex,removed,meta=executable_portfolio(port,capital,min_w,frac)
    orders,_=order_plan(port,capital,min_w,frac)
    sv=float(meta.get('stock_value',0)); cv=float(meta.get('cdi_value',0))
    stocks=sv/capital if capital else 0; cdi=cv/capital if capital else 0
    formation=first(ff,['data_formacao_forward','data_formacao_carteira']); pending=upcoming_formation(formation)
    if pending:
        st.info(f'Carteira preparada para {pd.to_datetime(formation).strftime("%d/%m/%Y")}, primeiro pregão do período. Valores ainda não representam compras realizadas.')
    st.subheader('Distribuição para '+money(capital))
    a,b=st.columns(2)
    a.metric(f'Ações · {pct(stocks)}',money(sv))
    b.metric(f'CDI / reserva · {pct(cdi)}',money(cv))
    stock_count=len(ex.loc[~ex.apply(is_cdi,axis=1)]) if not ex.empty else 0
    st.caption(f'{stock_count} ações no plano executável. Valores calculados com preços de referência de {format_updated_at(first(ff,["data_limite_dados_selecao"]))}; os preços no pregão podem variar.')
    st.subheader('Plano para o primeiro pregão' if pending else 'Plano de alocação')
    if orders.empty:
        st.info('Não há ordens calculadas para o valor informado.')
    else:
        columns=['Ativo','Quantidade','Valor estimado','Peso final','Preco','Empresa']
        st.dataframe(orders[columns].rename(columns={'Preco':'Preço ref.','Valor estimado':'Valor planejado','Peso final':'Peso'}),hide_index=True,use_container_width=True)
    st.download_button('Baixar carteira-base do modelo',data=f.forward.read_bytes(),file_name=f.forward.name,mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',icon=':material/download:',help='Arquivo oficial da seleção; não inclui ajustes do valor simulado acima.')
    if not removed.empty:
        names=', '.join(removed['ticker'].astype(str).str.replace('.SA','',regex=False))
        st.caption(f'Fora do plano executável com os ajustes atuais: {names}.')
    if ps:
        cr,_pa=checked_partial_return(ex,ps,assets,capital,min_w,frac)
        ib=first(ps,['retorno_ibov_parcial','retorno_ibov_periodo']); al=fnum(cr,np.nan)-fnum(ib,np.nan) if not np.isnan(fnum(cr,np.nan)) and not np.isnan(fnum(ib,np.nan)) else np.nan; dt=format_updated_at(first(ps,['data_avaliacao_parcial','data_avaliacao']),f.partial)
        status_txt=str(ps.get('status','')).lower(); period_title='Fechamento do mês' if 'fechamento' in status_txt else 'Acompanhamento do mês'
        st.markdown(f'#### {period_title} · dados até {dt}')
        p1,p2,p3=st.columns(3); p1.metric('Modelo executável',pct(cr)); p2.metric('IBOV',pct(ib)); p3.metric('Diferença',pct(al))
        if cdi and pd.isna(first(ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo'])):
            st.warning('CDI da reserva indisponivel; parcial executavel nao publicada.')
    with st.expander('Composição e critérios'):
        st.write(f'Mercado: {ff.get("regime_mercado") or "-"}. Seleção: {str(ff.get("sinal_usado") or "-").replace("_"," ").lower()}.')
        sfig=sector_exposure_chart(ex)
        if sfig is not None:
            st.plotly_chart(sfig,use_container_width=True)
    with st.expander('Detalhe de uma ação'):
        render_assets(port,capital,min_w,frac)
def practical_partial_return(ex,ps,assets):
    cdi_ret=platform_cdi_return(first(ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo']))
    return calculate_practical_partial_return(ex,assets,cdi_ret)
def checked_partial_return(ex,ps,assets,capital,min_w,frac):
    result,details=practical_partial_return(ex,ps,assets)
    if (ps.get('calculo_oficial')=='executavel_top15_capital_10000'
        and abs(capital-HISTORY_CAPITAL)<.01 and abs(min_w-.01)<1e-9 and frac):
        frozen=fnum(ps.get('retorno_modelo_executavel'))
        if np.isnan(result) or np.isnan(frozen) or not np.isclose(result,frozen,atol=1e-9,rtol=0):
            raise ValueError('Parcial executavel diverge do arquivo oficial; painel bloqueado ate reconciliacao')
    return result,details
def render_tracking(f,port,capital,min_w,frac):
    ps,assets=partial(f); ex,_,_=executable_portfolio(port,capital,min_w,frac); st.subheader('Acompanhamento do mês')
    if not ps:
        st.info('A primeira parcial deste mês aparecerá aqui depois do início da carteira e da publicação dos dados oficiais.')
        return
    ret,pa=checked_partial_return(ex,ps,assets,capital,min_w,frac); ib=first(ps,['retorno_ibov_parcial','retorno_ibov_periodo']); cd=platform_cdi_return(first(ps,['retorno_cdi_liquido_periodo'])); alfa=ret-fnum(ib,np.nan); dt=format_updated_at(first(ps,['data_avaliacao_parcial','data_avaliacao']),f.partial)
    c1,c2,c3,c4=st.columns(4); c1.metric('Minha carteira',pct(ret)); c2.metric('IBOV',pct(ib)); c3.metric('CDI líquido',pct(cd)); c4.metric('Diferença vs IBOV',pct(alfa)); st.markdown(f'<span class="pill">Dados até: {dt}</span>',unsafe_allow_html=True)
    if not ex.empty and ex.apply(is_cdi,axis=1).any() and pd.isna(first(ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo'])):
        st.warning('CDI da reserva indisponivel; parcial executavel nao publicada.')
    if not pa.empty:
        t=pd.DataFrame({'Ativo':pa.get('ticker'),'Peso real':pa.get('peso_executavel'),'Quantidade':pa.get('quantidade'),'Preco entrada':pa.get('preco_entrada'),'Preco atual':pa.get('preco_atual'),'Resultado':pa.get('retorno_pratico'),'Impacto':pa.get('contribuicao_pratica')})
        for c in ['Peso real','Resultado','Impacto']: t[c]=t[c].map(pct)
        for c in ['Preco entrada','Preco atual']: t[c]=t[c].map(money)
        t['Quantidade']=t['Quantidade'].map(lambda x:'-' if pd.isna(x) else str(int(x))); st.dataframe(t,hide_index=True,use_container_width=True)
def render_assets(port,capital,min_w,frac):
    ex,removed,_=executable_portfolio(port,capital,min_w,frac); stocks=ex[~ex.apply(is_cdi,axis=1)].copy() if not ex.empty else pd.DataFrame()
    if stocks.empty: st.info('Nao ha acoes compraveis.'); return
    ticker=st.selectbox('Escolha uma acao',[str(t) for t in stocks['ticker'].dropna()]); r=stocks[stocks['ticker'].astype(str).eq(ticker)].iloc[0]
    a,b=st.columns(2)
    a.metric('Quantidade',str(int(fnum(r.get('quantidade'),0))))
    b.metric('Valor planejado',money(r.get('valor_estimado')))
    st.caption(f'Peso executável: {pct(r.get("peso_executavel"))} · Preço de referência: {money(r.get("preco_referencia"))}')
def previous_portfolio_table(rows:pd.DataFrame)->pd.DataFrame:
    if rows.empty: return rows
    src=rows.copy()
    def col_value(names:list[str]):
        out=pd.Series([np.nan]*len(src),index=src.index,dtype='object')
        for name in names:
            if name in src.columns:
                out=out.combine_first(src[name])
        return out
    view=pd.DataFrame({
        'Ativo':col_value(['ticker']),
        'Empresa':col_value(['nome','empresa']),
        'Setor':col_value(['setor']),
        'Tipo':col_value(['tipo_alocacao','tipo_linha']),
        'Peso':col_value(['peso_executavel_total','peso_executavel','peso_final','peso_recomendado']),
        'Quantidade':col_value(['quantidade']),
        'Preco':col_value(['preco_referencia','preco_entrada','preco_atual']),
        'Valor':col_value(['valor_estimado','valor_executado','valor_alvo']),
        'Retorno':col_value(['retorno_periodo']),
        'Impacto':col_value(['contribuicao_executavel','contribuicao_executavel_total','contribuicao']),
    })
    cdi_mask=view['Ativo'].astype(str).str.upper().eq('CDI') | view['Tipo'].astype(str).str.lower().str.contains('cdi|caixa|reserva',regex=True,na=False)
    view['_ord']=np.where(cdi_mask,1,0)
    view=view.sort_values(['_ord','Ativo']).drop(columns=['_ord'])
    view['Peso']=view['Peso'].map(pct)
    view['Quantidade']=view['Quantidade'].map(lambda x:'-' if pd.isna(x) else str(int(fnum(x,0))))
    for c in ['Preco','Valor']:
        view[c]=view[c].map(money)
    for c in ['Retorno','Impacto']:
        view[c]=view[c].map(pct)
    return view.fillna('-').replace({'None':'-'})

def render_previous_portfolios(f):
    st.subheader('Carteiras anteriores'); hist=historical_executable_portfolios(f); perf=monthly(f)
    if hist.empty: st.info('Nenhuma carteira anterior encontrada.'); return
    hist['mes']=hist['mes'].astype(str).str[:7]; selected=st.selectbox('Escolha o mes',sorted(hist['mes'].dropna().unique(),reverse=True)); rows=hist[hist['mes'].eq(selected)].copy(); p=perf[perf['mes'].astype(str).str[:7].eq(selected)].head(1)
    if not p.empty:
        r=p.iloc[0].to_dict(); a,b,c,d=st.columns(4); a.metric('Carteira',pct(r.get('retorno_modelo'))); b.metric('IBOV',pct(r.get('retorno_expost_ibov'))); c.metric('CDI liquido',pct(r.get('retorno_cdi_liquido_periodo'))); d.metric('Diferenca vs IBOV',pct(r.get('alfa')))
    st.dataframe(previous_portfolio_table(rows),hide_index=True,use_container_width=True)
def render_history(f,capital,min_w,frac):
    st.subheader('Histórico do modelo executável'); df=monthly(f,capital,min_w,frac)
    st.caption('Histórico de jan/2021 a out/2026 refeito pela metodologia vigente aplicada retroativamente: é um backtest, não desempenho operado. A metodologia vigente foi escolhida após testes sobre estes mesmos meses.')
    previous_path,previous_ps=latest_previous_open_partial(f,df)
    if previous_path:
        month=str(previous_ps['mes'])[:7]
        model=fnum(previous_ps.get('retorno_modelo_executavel'))
        ibov=fnum(first(previous_ps,['retorno_ibov_parcial','retorno_ibov_periodo']))
        cdi=fnum(first(previous_ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo']))
        difference=model-ibov if not np.isnan(model) and not np.isnan(ibov) else np.nan
        st.subheader(f'Parcial de {month_label(month)}')
        cutoff=format_updated_at(first(previous_ps,['data_corte_comum','data_avaliacao_parcial']))
        st.caption(f'Dados até {cutoff} · referência de {money(HISTORY_CAPITAL)}. Ainda não é fechamento e não entra nos totais, médias ou gráficos abaixo.')
        a,b,c,d=st.columns(4)
        a.metric('Carteira',pct(model)); b.metric('IBOV',pct(ibov)); c.metric('CDI líquido',pct(cdi)); d.metric('Diferença vs IBOV',pct(difference))
        st.divider()
    if df.empty: st.info('Histórico fechado ainda não encontrado.'); return
    current_ps,_=partial(f)
    if current_ps:
        period_status='Fechado' if 'fechamento' in str(current_ps.get('status','')).lower() else 'Parcial'
        evaluated_at=str(first(current_ps,['data_avaliacao_parcial','data_avaliacao']) or '')[:10]
        st.caption(f'{month_label(current_forward_month(f))} | {period_status} ate {evaluated_at} | {f.display_name or f.version} | {money(capital)}. Meses anteriores: referencia historica de {money(HISTORY_CAPITAL)}.')
        current_row=df[df['mes'].astype(str).str[:7].eq(current_forward_month(f))]
        if not current_row.empty and fnum(current_row.iloc[-1].get('peso_cdi_executavel'),0)>0 and pd.isna(first(current_ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo'])):
            st.warning('CDI da reserva indisponivel nesta parcial; retorno da reserva considerado zero ate a atualizacao da fonte.')
    total_model=compound(df.get('retorno_modelo',pd.Series(dtype=float)))
    total_ibov=compound(df.get('retorno_expost_ibov',pd.Series(dtype=float)))
    total_alfa=total_model-total_ibov if not np.isnan(total_model) and not np.isnan(total_ibov) else np.nan
    hit_rate=pd.to_numeric(df.get('alfa',pd.Series(dtype=float)),errors='coerce').gt(0).mean()
    c1,c2,c3,c4=st.columns(4); c1.metric('Modelo executável',pct(total_model)); c2.metric('IBOV',pct(total_ibov)); c3.metric('Ganho acima do IBOV',pct(total_alfa)); c4.metric('Meses que bateu',pct(hit_rate))
    m_model=eq_month(df.get('retorno_modelo',pd.Series(dtype=float))); m_ibov=eq_month(df.get('retorno_expost_ibov',pd.Series(dtype=float))); m_cdi=eq_month(df.get('retorno_cdi_liquido_periodo',pd.Series(dtype=float)))
    st.subheader('Taxa média histórica'); a,b,c,d=st.columns(4); a.metric('Modelo ao mês',pct(m_model),delta=f'Ano: {pct((1+m_model)**12-1)}'); b.metric('IBOV ao mês',pct(m_ibov),delta=f'Ano: {pct((1+m_ibov)**12-1)}'); c.metric('CDI líquido ao mês',pct(m_cdi),delta=f'Ano: {pct((1+m_cdi)**12-1)}'); d.metric('Ganho médio vs IBOV',pct(m_model-m_ibov),delta=f'Ano: {pct((1+m_model)**12-(1+m_ibov)**12)}')
    st.subheader('Evolução acumulada')
    fig=cumulative(df)
    if fig is not None:
        st.plotly_chart(fig,use_container_width=True)
    html=historical_calendar_html(df,previous_ps if previous_path else None)
    if html:
        st.markdown(html,unsafe_allow_html=True)
    view=df.copy();
    for c in [c for c in view.columns if c.startswith('retorno') or c.startswith('alfa') or c.startswith('peso')]: view[c]=view[c].map(pct)
    cols=[c for c in ['mes','peso_acoes_executavel','peso_cdi_executavel','retorno_modelo','retorno_expost_ibov','retorno_cdi_liquido_periodo','alfa','bateu_ibov'] if c in view.columns]
    labels={'mes':'Mes','peso_acoes_executavel':'Acoes','peso_cdi_executavel':'CDI','retorno_modelo':'Carteira','retorno_expost_ibov':'IBOV no mes','retorno_cdi_liquido_periodo':'CDI liquido','alfa':'Diferenca vs IBOV','bateu_ibov':'Bateu IBOV'}
    with st.expander('Ver detalhamento mensal em linhas'):
        st.dataframe(view[cols].rename(columns=labels),hide_index=True,use_container_width=True,height=520)
    audit_cols=[c for c in ['mes','tipo_regime_expost','regime_previsto_norm','data_inicio_performance','data_avaliacao'] if c in view.columns]
    if audit_cols:
        with st.expander('Auditoria tecnica'):
            st.dataframe(view[audit_cols].rename(columns={'mes':'Mes','tipo_regime_expost':'Regime realizado','regime_previsto_norm':'Regime previsto','data_inicio_performance':'Inicio performance','data_avaliacao':'Fim performance'}),hide_index=True,use_container_width=True,height=360)
def render_method():
    st.write('A plataforma mostra a versão executável do modelo: quantidade inteira de ações, exclusão de posições pequenas e saldo em CDI líquido. A simulação usa preços de referência; não é uma ordem executada.')
def monthly_report_files()->list[Path]:
    found=list((ROOT/'output'/'pdf').glob('carteira_recomendada_20*_*.pdf'))
    final={p.stem for p in found if not p.stem.endswith('_parcial')}
    return sorted([p for p in found if not (p.stem.endswith('_parcial') and p.stem[:-8] in final)],reverse=True)
def render_reports_sidebar():
    reports=monthly_report_files()
    if not reports: return
    def label(path:Path)->str:
        m=re.search(r'(20\d{2})_(\d{2})',path.stem); return (month_label(f'{m.group(1)}-{m.group(2)}')+(' (parcial)' if path.stem.endswith('_parcial') else '')) if m else path.stem
    with st.sidebar.expander('Relatório mensal (PDF)'):
        chosen=st.selectbox('Mês',reports,format_func=label,label_visibility='collapsed')
        st.download_button('Baixar relatório',data=chosen.read_bytes(),file_name=chosen.name,mime='application/pdf',icon=':material/picture_as_pdf:',help='Carteira recomendada, desempenho do mês e gráfico de 12 meses de cada ação com os indicadores do robô.')
def main():
    try: f=files()
    except (OSError,ValueError,KeyError) as exc:
        st.error(f'Carteira oficial indisponivel: {exc}')
        st.stop()
    port=portfolio(f); css(); st.sidebar.title('Simular carteira'); capital=simulated_capital_input()
    with st.sidebar.expander('Ajustes da simulação'):
        frac=st.toggle('Permitir compra fracionária',value=True)
        min_w=st.slider('Remover pesos menores que',0.0,0.05,0.01,0.005,format='%.3f')
    with st.sidebar.expander('Atualização'):
        auto=st.toggle('Atualizar parcial automaticamente',value=True)
        mins=st.select_slider('Intervalo da parcial',options=[5,10,15,30,60],value=15)
    render_reports_sidebar()
    autorefresh(auto,mins)
    ff=fields(f.forward,'Resumo Forward')
    st.title(f'Carteira de {month_label(current_forward_month(f))}')
    st.caption(f'{f.display_name or f.version} · Dados de seleção até {format_updated_at(ff.get("data_limite_dados_selecao"))} · Formação em {format_updated_at(ff.get("data_formacao_forward"))}')
    tabs=st.tabs(['Carteira','Acompanhamento','Histórico','Carteiras anteriores'])
    with tabs[0]: render_start(f,port,capital,min_w,frac)
    with tabs[1]: render_tracking(f,port,capital,min_w,frac)
    with tabs[2]: render_history(f,capital,min_w,frac)
    with tabs[3]: render_previous_portfolios(f)
    with st.expander('Sobre o modelo e os dados'):
        render_method()
        st.write({'Carteira oficial':f.forward.name,'Modelo':f.display_name or f.version,'Identificador interno':f.version,'Parcial':f.partial.name if f.partial else 'nao encontrada','Historico executavel':f.operational.name if f.operational else 'nao encontrado'})
if __name__=='__main__': main()




