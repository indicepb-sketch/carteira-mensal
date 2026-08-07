from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
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
EXCEL_DIR=ROOT/'output'/'excel'
CDI_MONTHLY_PATH=ROOT/'data'/'processed'/'cdi_mensal_ipeadata.csv'
IBOV_MONTHLY_PATH=ROOT/'data'/'processed'/'ibov_mensal_oficial.csv'
SCENARIO='TOP15'; SCENARIO_LABEL='T49 - Top 15 operacional'; PLATFORM_MAX_STOCKS=15; HISTORY_CAPITAL=10000.0
SOURCE_CDI_IR=0.225; PLATFORM_CDI_IR=0.225
st.set_page_config(page_title='Carteira Mensal', page_icon='CM', layout='wide', initial_sidebar_state='expanded')
@dataclass(frozen=True)
class AppFiles:
    forward:Path|None; partial:Path|None; operational:Path|None
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
def files()->AppFiles:
    return AppFiles(latest('carteira_forward_2026_*.xlsx'), latest('parcial_carteira_forward_2026_*.xlsx'), latest('shadow_teste49_top15_regime_capital.xlsx') or latest('shadow_teste46_carteira_executavel.xlsx') or latest('shadow_teste45_consolidacao_final_t44a.xlsx'))
@st.cache_data(show_spinner=False)
def sheet(path:str,name:str)->pd.DataFrame:
    try: return pd.read_excel(path,sheet_name=name)
    except Exception: return pd.DataFrame()
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
    n=fnum(x); return '-' if np.isnan(n) else f'{n*100:.{d}f}%'
def money(x:Any)->str:
    n=fnum(x)
    return '-' if np.isnan(n) else f'R$ {n:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
def first(d:dict[str,Any],keys:list[str])->Any:
    for k in keys:
        v=d.get(k)
        if v is not None and not pd.isna(v): return v
    return None
def platform_cdi_return(v:Any)->float:
    net=fnum(v,0.0)
    if np.isnan(net): return 0.0
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
        return f"{parsed.strftime('%d/%m/%Y')} {file_dt.strftime('%H:%M')}" if file_dt else parsed.strftime('%d/%m/%Y')
    return file_dt.strftime('%d/%m/%Y %H:%M') if file_dt else '-'
def is_cdi(row:pd.Series)->bool:
    txt=' '.join(str(row.get(c,'')).lower() for c in ['ticker','setor','tipo_alocacao','nome'])
    return 'cdi' in txt or 'caixa' in txt or 'reserva' in txt
def norm(df:pd.DataFrame,col='peso_recomendado')->pd.DataFrame:
    out=df.copy()
    if col in out.columns:
        out[col]=pd.to_numeric(out[col],errors='coerce').fillna(0.0)
        if out[col].sum()>1.5: out[col]=out[col]/100.0
    return out
def portfolio(f:AppFiles)->pd.DataFrame:
    if not f.forward: return pd.DataFrame()
    df=sheet(str(f.forward),'Carteira Aplicada')
    if df.empty: df=sheet(str(f.forward),'Carteira Forward')
    if df.empty: return df
    df=norm(df)
    if 'ticker' in df: df['ticker']=df['ticker'].astype(str).str.upper()
    return df
def rank_top(stocks:pd.DataFrame)->pd.DataFrame:
    r=stocks.copy()
    r['_nota']=pd.to_numeric(r.get('nota_final',pd.Series(index=r.index)),errors='coerce').fillna(-999)
    r['_peso']=pd.to_numeric(r.get('peso_recomendado',pd.Series(index=r.index)),errors='coerce').fillna(0)
    return r.sort_values(['_nota','_peso'],ascending=[False,False])
def executable_portfolio(df:pd.DataFrame,capital:float,min_weight:float,fractional:bool):
    if df.empty: return pd.DataFrame(),pd.DataFrame(),{'stock_value':0.0,'cdi_value':capital}
    data=norm(df); cash=data[data.apply(is_cdi,axis=1)].copy(); stocks=data[~data.apply(is_cdi,axis=1)].copy()
    price_col='preco_entrada_fechamento_mes_anterior' if 'preco_entrada_fechamento_mes_anterior' in stocks.columns else 'preco_entrada' if 'preco_entrada' in stocks.columns else None
    stocks['preco_referencia']=pd.to_numeric(stocks[price_col],errors='coerce') if price_col else np.nan
    if len(stocks)>PLATFORM_MAX_STOCKS:
        keep=rank_top(stocks).head(PLATFORM_MAX_STOCKS).index; removed_top=stocks.loc[~stocks.index.isin(keep)].copy(); stocks=stocks.loc[keep].copy()
    else: removed_top=stocks.iloc[0:0].copy()
    small=stocks[pd.to_numeric(stocks['peso_recomendado'],errors='coerce').fillna(0)<min_weight].copy(); stocks=stocks.drop(index=small.index)
    removed=pd.concat([removed_top,small],ignore_index=True)
    if stocks.empty:
        return pd.DataFrame([{'ticker':'CDI','nome':'Reserva/CDI liquido','setor':'Protecao','peso_executavel':1.0,'valor_estimado':capital,'forma':'aplicacao + sobra'}]),removed,{'stock_value':0.0,'cdi_value':capital}
    tw=pd.to_numeric(stocks['peso_recomendado'],errors='coerce').sum(); target=capital*min(tw,1.0)
    stocks['peso_na_parte_acoes']=pd.to_numeric(stocks['peso_recomendado'],errors='coerce')/tw; stocks['valor_alvo']=target*stocks['peso_na_parte_acoes']
    raw=stocks['valor_alvo']/stocks['preco_referencia']; stocks['quantidade']=np.floor(raw if fractional else raw/100.0)* (1 if fractional else 100)
    stocks['valor_estimado']=stocks['quantidade'].fillna(0)*stocks['preco_referencia'].fillna(0); stocks=stocks[stocks['quantidade'].fillna(0)>0].copy()
    stock_value=float(stocks['valor_estimado'].sum()); cdi_value=max(capital-stock_value,0.0)
    stocks['peso_executavel']=stocks['valor_estimado']/capital if capital else 0.0; stocks['peso_modelo']=stocks['peso_recomendado']; stocks['forma']='fracionario' if fractional else 'lote padrao'
    cdi=pd.DataFrame([{'ticker':'CDI','nome':'Reserva/CDI liquido','setor':'Protecao','peso_executavel':cdi_value/capital if capital else 0.0,'valor_estimado':cdi_value,'forma':'aplicacao + sobra'}])
    return pd.concat([stocks,cdi],ignore_index=True,sort=False),removed,{'stock_value':stock_value,'cdi_value':cdi_value}
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
def finalized_partial_portfolio_rows(f,partial_path:Path|None=None)->pd.DataFrame:
    partial_path=partial_path or f.partial
    if not partial_path: return pd.DataFrame()
    ps=fields(partial_path,'Resumo Parcial')
    if 'fechamento' not in str(ps.get('status','')).lower(): return pd.DataFrame()
    mes=str(ps.get('mes') or ps.get('mes_referencia') or '')[:7]
    if not mes: return pd.DataFrame()
    fp=finalized_partial_forward(ps,mes)
    if not fp: return pd.DataFrame()
    base=sheet(str(fp),'Carteira Aplicada')
    if base.empty: base=sheet(str(fp),'Carteira Forward')
    if base.empty: return pd.DataFrame()
    ex,_,_=executable_portfolio(base,HISTORY_CAPITAL,0.01,True)
    if ex.empty: return pd.DataFrame()
    assets=sheet(str(partial_path),'Ativos')
    out=ex.copy(); out['ticker']=out['ticker'].astype(str).str.upper()
    if not assets.empty and 'ticker' in assets:
        src=assets.copy(); src['ticker_key']=src['ticker'].astype(str).str.upper()
        out['ticker_key']=out['ticker'].astype(str).str.upper()
        cols=[c for c in ['ticker_key','retorno_periodo','preco_entrada','preco_atual','data_avaliacao'] if c in src.columns]
        out=out.merge(src[cols],on='ticker_key',how='left')
    cdi_ret=platform_cdi_return(first(ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo']))
    out['retorno_periodo']=pd.to_numeric(out.get('retorno_periodo',pd.Series(index=out.index)),errors='coerce')
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
def finalized_partial_month_row(f,partial_path:Path|None=None)->pd.DataFrame:
    partial_path=partial_path or f.partial
    if not partial_path: return pd.DataFrame()
    ps=fields(partial_path,'Resumo Parcial')
    status=str(ps.get('status','')).lower()
    if 'fechamento' not in status: return pd.DataFrame()
    mes=str(ps.get('mes') or ps.get('mes_referencia') or '')[:7]
    if not mes: return pd.DataFrame()
    rows=finalized_partial_portfolio_rows(f,partial_path)
    stock_value=float(pd.to_numeric(rows.loc[~rows.apply(is_cdi,axis=1),'valor_estimado'],errors='coerce').sum()) if not rows.empty and 'valor_estimado' in rows.columns else np.nan
    cdi_value=float(pd.to_numeric(rows.loc[rows.apply(is_cdi,axis=1),'valor_estimado'],errors='coerce').sum()) if not rows.empty and 'valor_estimado' in rows.columns else np.nan
    ret_pratico=np.nan
    if not rows.empty and 'contribuicao_executavel' in rows.columns:
        ret_pratico=float(pd.to_numeric(rows['contribuicao_executavel'],errors='coerce').fillna(0).sum())
    row={
        'mes':mes,
        'retorno_modelo':ret_pratico if not np.isnan(ret_pratico) else fnum(first(ps,['retorno_carteira_parcial_aplicada','retorno_carteira_periodo']),np.nan),
        'retorno_expost_ibov':fnum(first(ps,['retorno_ibov_parcial','retorno_ibov_periodo']),np.nan),
        'retorno_cdi_liquido_periodo':fnum(first(ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo']),np.nan),
        'peso_acoes_executavel':stock_value/HISTORY_CAPITAL if not np.isnan(stock_value) else fnum(first(ps,['exposicao_acoes','peso_acoes']),np.nan),
        'peso_cdi_executavel':cdi_value/HISTORY_CAPITAL if not np.isnan(cdi_value) else fnum(first(ps,['peso_defensivo_cdi','peso_cdi']),np.nan),
        'tipo_regime_expost':'fechamento_mes',
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
def monthly(f):
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
    if not extra.empty and 'mes' in df.columns:
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
def historical_calendar_html(df):
    months=[(1,'JAN'),(2,'FEV'),(3,'MAR'),(4,'ABR'),(5,'MAI'),(6,'JUN'),(7,'JUL'),(8,'AGO'),(9,'SET'),(10,'OUT'),(11,'NOV'),(12,'DEZ')]
    data=df.copy(); data['mes_dt']=pd.to_datetime(data.get('mes'),errors='coerce'); data=data.dropna(subset=['mes_dt'])
    if data.empty: return ''
    data['ano']=data['mes_dt'].dt.year; data['mes_num']=data['mes_dt'].dt.month
    for c in ['retorno_modelo','retorno_expost_ibov','alfa']: data[c]=pd.to_numeric(data.get(c),errors='coerce')
    html=['<div class="hist-card"><div class="hist-title">Rentabilidade Historica</div><table class="hist-table"><thead><tr><th>ANO</th>']+[f'<th>{m}</th>' for _,m in months]+['<th>TOTAL</th></tr></thead><tbody>']
    for year in sorted(data['ano'].astype(int).unique(),reverse=True):
        ydf=data[data['ano'].eq(year)].copy(); html.append(f'<tr><th>{year}</th>')
        for mn,_ in months:
            row=ydf[ydf['mes_num'].eq(mn)]
            if row.empty: html.append('<td class="empty">-</td>'); continue
            r=row.iloc[0]; ret=fnum(r.get('retorno_modelo')); ibov=fnum(r.get('retorno_expost_ibov')); alfa=fnum(r.get('alfa')); rc='pos' if not np.isnan(ret) and ret>=0 else 'neg'; ic='pos' if not np.isnan(ibov) and ibov>=0 else 'neg'; ac='beat' if not np.isnan(alfa) and alfa>=0 else 'miss' if not np.isnan(alfa) else 'neutral'
            ibov_txt='-' if np.isnan(ibov) else f'IBOV {ibov*100:+.2f}%'
            html.append(f'<td class="hist-cell"><div class="hist-ret {rc}">{ret*100:.2f}%</div><div class="hist-ibov {ic}">{ibov_txt}</div><div class="hist-sub {ac}">{alfa*100:+.2f} p.p. vs IBOV</div></td>')
        tm=compound(ydf.get('retorno_modelo',pd.Series(dtype=float))); ti=compound(ydf.get('retorno_expost_ibov',pd.Series(dtype=float))); ta=tm-ti if not np.isnan(tm) and not np.isnan(ti) else np.nan; rc='pos' if not np.isnan(tm) and tm>=0 else 'neg'; ic='pos' if not np.isnan(ti) and ti>=0 else 'neg'; ac='beat' if not np.isnan(ta) and ta>=0 else 'miss'
        ti_txt='-' if np.isnan(ti) else f'IBOV {ti*100:+.2f}%'
        html.append(f'<td class="total hist-cell"><div class="hist-ret {rc}">{tm*100:.2f}%</div><div class="hist-ibov {ic}">{ti_txt}</div><div class="hist-sub {ac}">{ta*100:+.2f} p.p. vs IBOV</div></td></tr>')
    html.append('</tbody></table></div>'); return ''.join(html)
def autorefresh(enabled: bool, minutes: int):
    if enabled:
        components.html(f"<script>setTimeout(function(){{window.parent.location.reload();}}, {minutes * 60000});</script>", height=0)
def css():
    st.markdown('''<style>.block-container{padding-top:1.1rem;max-width:1480px}.hero{background:linear-gradient(135deg,#10233d,#142f2d);border:1px solid #2f5670;border-radius:8px;padding:20px 22px;margin:10px 0 16px}.portfolio-ref{font-size:1.35rem;font-weight:850;color:#f8fafc;margin-bottom:8px}.note{color:#a7b0c0;line-height:1.55}.decision{background:#0f2d22;color:#ecfdf5;border:1px solid #2f855a;border-radius:8px;padding:16px 18px;margin-bottom:12px;line-height:1.55}.warn{background:#32220d;color:#fff7ed;border:1px solid #b45309;border-radius:8px;padding:14px 16px;margin:10px 0}.card{background:#111827;color:#f8fafc;border:1px solid #2f3b52;border-radius:8px;padding:14px 16px}.pill{display:inline-block;padding:4px 10px;border-radius:999px;background:#1e293b;color:#dbeafe;border:1px solid #334155;font-weight:700;font-size:.85rem}.hist-card{background:#0f172a;color:#f8fafc;border:1px solid #263449;border-radius:12px;margin:18px 0;overflow:hidden}.hist-title{font-weight:800;font-size:1.1rem;padding:18px 24px;border-bottom:1px solid #263449}.hist-table{width:100%;border-collapse:separate;border-spacing:0;font-size:.92rem}.hist-table th{background:#111827;color:#dbeafe;text-align:center;padding:13px 10px;font-weight:800;border-bottom:1px solid #263449}.hist-table td{background:#0b1220;text-align:center;padding:13px 9px;border-top:1px solid #182235;border-left:1px solid #111827;min-width:82px}.hist-table tbody th{background:#111827;color:#f8fafc}.hist-table .total{background:#101b2e}.hist-ret{font-weight:800}.hist-ret.pos{color:#4ade80}.hist-ret.neg{color:#f87171}.hist-ibov{font-size:.78rem;color:#cbd5e1;margin-top:4px}.hist-ibov.pos{color:#93c5fd}.hist-ibov.neg{color:#fca5a5}.hist-sub{font-size:.78rem;color:#cbd5e1;margin-top:4px;min-height:17px}.hist-sub.beat{color:#86efac;font-weight:700}.hist-sub.miss{color:#fca5a5;font-weight:700}.hist-table .empty{color:#94a3b8;font-weight:800}div[data-testid="stMetric"]{background:#111827;border:1px solid #2f3b52;border-radius:8px;padding:14px 16px;min-height:120px}div[data-testid="stMetric"] label,div[data-testid="stMetric"] [data-testid="stMetricValue"]{color:#f8fafc!important}</style>''',unsafe_allow_html=True)
def render_start(f,port,capital,min_w,frac):
    ff,rg=fields(f.forward,'Resumo Forward'),fields(f.forward,'Regime Mercado Base'); ps,assets=partial(f); ex,removed,meta=executable_portfolio(port,capital,min_w,frac); orders,_=order_plan(port,capital,min_w,frac); sv=float(meta.get('stock_value',0)); cv=float(meta.get('cdi_value',0)); stocks=sv/capital if capital else 0; cdi=cv/capital if capital else 0
    st.subheader('O que fazer agora'); st.markdown(f'<div class="decision">Com <b>{money(capital)}</b>, a carteira executavel fica com <b>{money(sv)}</b> em acoes e <b>{money(cv)}</b> em CDI liquido/reserva.</div>',unsafe_allow_html=True)
    a,b,c,d=st.columns(4); a.metric('Acoes',pct(stocks)); b.metric('CDI',pct(cdi)); c.metric('Comprar em acoes',money(sv)); d.metric('Aplicar em CDI',money(cv))
    if ps:
        cr,_pa=practical_partial_return(ex,ps,assets) if not ex.empty and not assets.empty else (first(ps,['retorno_carteira_parcial_aplicada','retorno_carteira_periodo']),pd.DataFrame())
        ib=first(ps,['retorno_ibov_parcial','retorno_ibov_periodo']); al=fnum(cr,np.nan)-fnum(ib,np.nan) if not np.isnan(fnum(cr,np.nan)) and not np.isnan(fnum(ib,np.nan)) else first(ps,['alfa_parcial_vs_ibov','alfa_vs_ibov']); dt=format_updated_at(first(ps,['data_avaliacao_parcial','data_avaliacao']),f.partial)
        status_txt=str(ps.get('status','')).lower(); period_title='Fechamento do mes' if 'fechamento' in status_txt else 'Acompanhamento do mes'
        st.markdown(f'#### {period_title}'); p1,p2,p3,p4=st.columns(4); p1.metric('Modelo executavel',pct(cr)); p2.metric('IBOV',pct(ib)); p3.metric('Diferenca',pct(al)); p4.metric('Atualizado em',dt)
    left,right=st.columns([.9,1.35])
    with left:
        st.markdown('#### Divisao executavel')
        fig=allocation_chart(stocks,cdi)
        if fig is not None:
            st.plotly_chart(fig,use_container_width=True)
        st.markdown('#### Exposicao por setor')
        sfig=sector_exposure_chart(ex)
        if sfig is not None:
            st.plotly_chart(sfig,use_container_width=True)
    with right:
        mercado=rg.get('regime_mercado') or rg.get('subtipo_mercado_favoravel') or 'Mercado seletivo'; st.markdown('#### Leitura simples'); st.dataframe(pd.DataFrame({'Pergunta':['Mes','Data de formacao','Leitura do mercado','Tipo de selecao','Carteira usada na plataforma'],'Resposta':[ff.get('mes_forward') or ff.get('mes_referencia') or current_forward_month(f) or '-' ,ff.get('data_formacao_forward') or ff.get('data_formacao_carteira'),mercado,ff.get('sinal_usado') or 'modelo operacional','Executavel por quantidade inteira']}),hide_index=True,use_container_width=True)
    st.subheader('Plano pratico de compra'); st.dataframe(orders,hide_index=True,use_container_width=True)
    if not removed.empty: st.markdown(f'<div class="warn">{len(removed)} ativo(s) nao entraram por peso operacional irrelevante ou limite operacional.</div>',unsafe_allow_html=True)
def practical_partial_return(ex,ps,assets):
    if ex.empty: return np.nan,pd.DataFrame()
    out=ex.copy()
    if not assets.empty and 'ticker' in assets:
        src=assets.copy(); src['ticker_key']=src['ticker'].astype(str).str.upper(); out['ticker_key']=out['ticker'].astype(str).str.upper(); cols=[c for c in ['ticker_key','retorno_periodo','preco_entrada','preco_atual'] if c in src.columns]; out=out.merge(src[cols],on='ticker_key',how='left')
    cdi_ret=platform_cdi_return(first(ps,['retorno_cdi_liquido_periodo','retorno_cdi_periodo'])); out['retorno_pratico']=pd.to_numeric(out.get('retorno_periodo',pd.Series(dtype=float)),errors='coerce'); out.loc[out.apply(is_cdi,axis=1),'retorno_pratico']=cdi_ret; out['retorno_pratico']=out['retorno_pratico'].fillna(0); out['contribuicao_pratica']=pd.to_numeric(out['peso_executavel'],errors='coerce').fillna(0)*out['retorno_pratico']; return float(out['contribuicao_pratica'].sum()),out
def render_tracking(f,port,capital,min_w,frac):
    ps,assets=partial(f); ex,_,_=executable_portfolio(port,capital,min_w,frac); st.subheader('Acompanhamento do mes')
    if not ps: st.info('Ainda nao ha parcial carregada.'); return
    ret,pa=practical_partial_return(ex,ps,assets); ib=first(ps,['retorno_ibov_parcial','retorno_ibov_periodo']); cd=platform_cdi_return(first(ps,['retorno_cdi_liquido_periodo'])); alfa=ret-fnum(ib,np.nan); dt=format_updated_at(first(ps,['data_avaliacao_parcial','data_avaliacao']),f.partial)
    c1,c2,c3,c4=st.columns(4); c1.metric('Minha carteira',pct(ret)); c2.metric('IBOV',pct(ib)); c3.metric('CDI liquido',pct(cd)); c4.metric('Diferenca vs IBOV',pct(alfa)); st.markdown(f'<span class="pill">Atualizado em: {dt}</span>',unsafe_allow_html=True)
    if not pa.empty:
        t=pd.DataFrame({'Ativo':pa.get('ticker'),'Peso real':pa.get('peso_executavel'),'Quantidade':pa.get('quantidade'),'Preco entrada':pa.get('preco_entrada'),'Preco atual':pa.get('preco_atual'),'Resultado':pa.get('retorno_pratico'),'Impacto':pa.get('contribuicao_pratica')})
        for c in ['Peso real','Resultado','Impacto']: t[c]=t[c].map(pct)
        for c in ['Preco entrada','Preco atual']: t[c]=t[c].map(money)
        t['Quantidade']=t['Quantidade'].map(lambda x:'-' if pd.isna(x) else str(int(x))); st.dataframe(t,hide_index=True,use_container_width=True)
def render_assets(port,capital,min_w,frac):
    st.subheader('Acoes da carteira executavel'); ex,removed,_=executable_portfolio(port,capital,min_w,frac); stocks=ex[~ex.apply(is_cdi,axis=1)].copy() if not ex.empty else pd.DataFrame()
    if stocks.empty: st.info('Nao ha acoes compraveis.'); return
    ticker=st.selectbox('Escolha uma acao',[str(t) for t in stocks['ticker'].dropna()]); r=stocks[stocks['ticker'].astype(str).eq(ticker)].iloc[0]
    st.markdown(f'<div class="card"><b>{ticker}</b> entrou com <b>{int(fnum(r.get("quantidade"),0))} acao(oes)</b>, peso real de <b>{pct(r.get("peso_executavel"))}</b> e valor de <b>{money(r.get("valor_estimado"))}</b>.</div>',unsafe_allow_html=True)
    a,b,c,d=st.columns(4); a.metric('Peso real',pct(r.get('peso_executavel'))); b.metric('Quantidade',str(int(fnum(r.get('quantidade'),0)))); c.metric('Preco referencia',money(r.get('preco_referencia'))); d.metric('Peso modelo',pct(r.get('peso_modelo')))
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
def render_history(f):
    st.subheader('Historico do modelo executavel'); df=monthly(f)
    if df.empty: st.info('Historico nao encontrado.'); return
    total_model=compound(df.get('retorno_modelo',pd.Series(dtype=float)))
    total_ibov=compound(df.get('retorno_expost_ibov',pd.Series(dtype=float)))
    total_alfa=total_model-total_ibov if not np.isnan(total_model) and not np.isnan(total_ibov) else np.nan
    hit_rate=pd.to_numeric(df.get('alfa',pd.Series(dtype=float)),errors='coerce').gt(0).mean()
    c1,c2,c3,c4=st.columns(4); c1.metric('Modelo executavel',pct(total_model)); c2.metric('IBOV',pct(total_ibov)); c3.metric('Ganho acima do IBOV',pct(total_alfa)); c4.metric('Meses que bateu',pct(hit_rate))
    m_model=eq_month(df.get('retorno_modelo',pd.Series(dtype=float))); m_ibov=eq_month(df.get('retorno_expost_ibov',pd.Series(dtype=float))); m_cdi=eq_month(df.get('retorno_cdi_liquido_periodo',pd.Series(dtype=float)))
    st.subheader('Taxa media historica'); a,b,c,d=st.columns(4); a.metric('Modelo ao mes',pct(m_model),delta=f'Ano: {pct((1+m_model)**12-1)}'); b.metric('IBOV ao mes',pct(m_ibov),delta=f'Ano: {pct((1+m_ibov)**12-1)}'); c.metric('CDI liquido ao mes',pct(m_cdi),delta=f'Ano: {pct((1+m_cdi)**12-1)}'); d.metric('Ganho medio vs IBOV',pct(m_model-m_ibov),delta=f'Ano: {pct((1+m_model)**12-(1+m_ibov)**12)}')
    st.subheader('Evolucao acumulada')
    fig=cumulative(df)
    if fig is not None:
        st.plotly_chart(fig,use_container_width=True)
    html=historical_calendar_html(df)
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
    st.subheader('Como funciona'); st.markdown('<div class="note">A plataforma mostra a versao executavel do modelo: compras em quantidade inteira, posicoes pequenas removidas e sobra aplicada em CDI liquido com IR mensal de 22,5%.</div>',unsafe_allow_html=True)
def main():
    f=files(); port=portfolio(f); css(); st.sidebar.title('Minha carteira'); capital=st.sidebar.number_input('Valor para simular',min_value=1000.0,value=10000.0,step=500.0,format='%.2f'); frac=st.sidebar.toggle('Permitir compra fracionaria',value=True); min_w=st.sidebar.slider('Remover pesos menores que',0.0,0.05,0.01,0.005,format='%.3f'); auto=st.sidebar.toggle('Atualizar parcial automaticamente',value=True); mins=st.sidebar.select_slider('Intervalo da parcial',options=[5,10,15,30,60],value=15); autorefresh(auto,mins)
    ref=portfolio_reference(f)
    st.title('Carteira mensal executavel'); st.markdown(f'<div class="hero"><div class="portfolio-ref">{ref}</div><b>Modelo atual:</b> {SCENARIO_LABEL}<br><span class="note">Carteira Top 15 convertida para compras reais: quantidade inteira de acoes, posicoes irrelevantes removidas e sobra em CDI liquido.</span></div>',unsafe_allow_html=True)
    with st.expander('Arquivos carregados'): st.write({'Carteira do mes':f.forward.name if f.forward else 'nao encontrada','Parcial':f.partial.name if f.partial else 'nao encontrada','Historico executavel':f.operational.name if f.operational else 'nao encontrado'})
    tabs=st.tabs(['O que fazer agora','Acompanhamento','Acoes da carteira','Historico','Carteiras anteriores','Como funciona'])
    with tabs[0]: render_start(f,port,capital,min_w,frac)
    with tabs[1]: render_tracking(f,port,capital,min_w,frac)
    with tabs[2]: render_assets(port,capital,min_w,frac)
    with tabs[3]: render_history(f)
    with tabs[4]: render_previous_portfolios(f)
    with tabs[5]: render_method()
if __name__=='__main__': main()




