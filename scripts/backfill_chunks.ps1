# 残り chunks (2-11) 順次バックフィル
$py = "C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe"
$env:PYTHONPATH = "C:\boat_project\boatrace-analysis"
$env:PYTHONIOENCODING = "utf-8"
$script = "C:\boat_project\boatrace-analysis\scripts\backfill_official.py"

# chunk定義: (start, end, chunkID)
$chunks = @(
    @("2023-03-02", "2023-05-19", "3"),
    @("2023-05-20", "2023-08-06", "4"),
    @("2023-08-07", "2023-10-24", "5"),
    @("2023-10-25", "2024-01-04", "6"),
    @("2024-01-10", "2024-03-28", "7"),
    @("2024-03-29", "2024-06-15", "8"),
    @("2024-06-16", "2024-09-02", "9"),
    @("2024-09-03", "2024-11-20", "10"),
    @("2024-11-21", "2025-05-07", "11")
)

foreach ($c in $chunks) {
    $start, $end, $id = $c
    $logfile = "C:\boat_project\boatrace-analysis\logs\backfill_chunk${id}.log"
    Write-Host "$([DateTime]::Now): chunk $id : $start .. $end"
    & $py $script --start $start --end $end --skip-existing --log-file $logfile 2>&1 | Select-Object -Last 3
    Write-Host "$([DateTime]::Now): chunk $id done"
}
Write-Host "ALL CHUNKS DONE"
