# Deploy beta gratuito

Este projeto esta preparado para rodar como beta no Streamlit Community Cloud.

## 1. Publicar no GitHub

Suba o repositorio para o GitHub. Antes de publicar, confira se `.venv/` e arquivos de credenciais locais nao foram versionados.

## 2. Criar app no Streamlit Community Cloud

No Streamlit Community Cloud:

1. Clique em **Create app**.
2. Selecione o repositorio.
3. Use o branch principal.
4. Use como arquivo principal:

```text
streamlit_app.py
```

O arquivo `streamlit_app.py` apenas chama a plataforma em:

```text
carteira_mensal/app/streamlit_app_user.py
```

## 3. Atualizacao diaria automatica

O workflow GitHub Actions fica em:

```text
.github/workflows/atualizacao-diaria.yml
```

Ele roda de segunda a sexta, as 23:00 UTC, aproximadamente 20:00 no horario de Fortaleza/Brasilia sem horario de verao.

Fluxo:

```text
GitHub Actions
  -> scripts/atualizacao_diaria.py
  -> forma a carteira forward se ainda nao existir
  -> atualiza a parcial do mes com yfinance e CDI SGS/BCB
  -> faz commit dos arquivos atualizados
  -> Streamlit Cloud recarrega os dados
```

## 4. Rodar manualmente

Tambem da para rodar pelo GitHub em **Actions > Atualizacao diaria da carteira > Run workflow**.

Opcionalmente informe `mes`, por exemplo:

```text
2026-08
```

## 5. Comandos locais equivalentes

Dentro de `carteira_mensal`:

```bash
python scripts/atualizacao_diaria.py --mes 2026-08 --allow-network
streamlit run app/streamlit_app_user.py
```

## Observacoes

- O beta gratuito depende das cotas gratuitas do GitHub Actions e Streamlit Community Cloud.
- O app usa arquivos Excel/CSV versionados como base de exibicao.
- Se futuramente houver chaves de API, elas devem ir para secrets do GitHub/Streamlit, nunca para o repositorio.
