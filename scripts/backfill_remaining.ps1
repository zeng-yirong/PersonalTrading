# Resume the remaining Tushare backfill as a detached process.
# Runs the high-value datasets first, then the bulk reference tables, so a
# daily-quota stop leaves the useful data in place.
$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath 'D:\onedrive.edu\OneDrive - hrbeu.edu.cn\VScodeFiles\AI4Trade\PersonalTrading'
$env:PYTHONIOENCODING = 'utf-8'

$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
$sync = Join-Path (Get-Location) 'scripts\sync_market.py'

# Stage A: everything that is still date-shaped (~5.7k calls)
& $python $sync backfill --datasets stk_factor,block_trade,dividend,new_share,ths_index --years 10 --workers 6 *>&1 |
    Tee-Object -FilePath 'data\backfill_a.log' -Append

# Stage B: per-symbol reference tables (~22k calls)
& $python $sync backfill --datasets stk_holdernumber,stk_holdertrade,share_float,stock_company --years 10 --workers 6 *>&1 |
    Tee-Object -FilePath 'data\backfill_b.log' -Append

"FINISHED $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath 'data\backfill_done.txt' -Encoding UTF8
