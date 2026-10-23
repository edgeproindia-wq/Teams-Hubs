param([string]$Message = "Update")
Set-Location $PSScriptRoot
git add -A
if (git status --porcelain) { git commit -m $Message } else { Write-Host "No new changes to commit." -ForegroundColor Yellow }
git push origin main
if ($LASTEXITCODE -ne 0) { Write-Host "Push failed - see the error above." -ForegroundColor Red; exit 1 }
$hook = $env:RENDER_DEPLOY_HOOK
if ($hook) {
  try { Invoke-RestMethod -Uri $hook -Method Post | Out-Null; Write-Host "Render deploy triggered." -ForegroundColor Green }
  catch { Write-Host "Could not call the deploy hook: $($_.Exception.Message)" -ForegroundColor Red }
} else {
  Write-Host "Pushed. Render deploys by itself (Auto-Deploy: On Commit). Watch Events in Render." -ForegroundColor Green
}
