# Finish the remaining reference datasets, highest value first.
# Detached on purpose: the earlier in-session job was killed when the session ended.
$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath 'D:\onedrive.edu\OneDrive - hrbeu.edu.cn\VScodeFiles\AI4Trade\PersonalTrading'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
$sync = Join-Path (Get-Location) 'scripts\sync_market.py'

function Run-Stage([string]$name, [string]$datasets) {
    "[$(Get-Date -Format 'HH:mm:ss')] stage $name start" | Out-File -FilePath 'data\remaining.log' -Append -Encoding UTF8
    & $python $sync backfill --datasets $datasets --years 10 --workers 6 *>&1 |
        Tee-Object -FilePath 'data\remaining.log' -Append
    "[$(Get-Date -Format 'HH:mm:ss')] stage $name exit=$LASTEXITCODE" | Out-File -FilePath 'data\remaining.log' -Append -Encoding UTF8
}

# Events first: these are what the 事件驱动 analyst actually reads.
Run-Stage 'events' 'dividend,block_trade,share_float,stk_holdertrade'
# Then reference tables.
Run-Stage 'reference' 'ths_index,new_share,stock_company'

"FINISHED $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath 'data\remaining_done.txt' -Encoding UTF8
