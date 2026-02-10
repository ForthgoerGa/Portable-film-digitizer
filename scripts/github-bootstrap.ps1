param(
    [Parameter(Mandatory = $true)]
    [string]$GitHubUsername,

    [string]$RepoName = "ece-445-film-digitizer",

    [ValidateSet("private", "public")]
    [string]$Visibility = "private",

    [string]$Collaborator = "Allannn-sudo"
)

$ghPath = "C:\Program Files\GitHub CLI\gh.exe"
if (-not (Test-Path $ghPath)) {
    throw "GitHub CLI not found at '$ghPath'. Install it or update this script path."
}

& $ghPath auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run: gh auth login"
}

Write-Host "Creating repo '$RepoName' as $Visibility and pushing current branch..."
$visibilityFlag = if ($Visibility -eq "private") { "--private" } else { "--public" }
& $ghPath repo create $RepoName $visibilityFlag --source . --remote origin --push
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create/push repository."
}

Write-Host "Inviting collaborator '$Collaborator' with push permission..."
& $ghPath api -X PUT "repos/$GitHubUsername/$RepoName/collaborators/$Collaborator" -f permission=push
if ($LASTEXITCODE -ne 0) {
    throw "Failed to invite collaborator."
}

Write-Host "Done. Repository created and collaborator invited."
