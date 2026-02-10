# GitHub Setup

This guide takes the local project in this folder and publishes it to GitHub.

## 1. Initialize Git Locally (Already Done in This Workspace)
```powershell
git init -b main
git add .
git commit -m "Initial project scaffold"
```

## 2. Authenticate GitHub CLI
In this environment, `gh` is installed at `C:\Program Files\GitHub CLI\gh.exe`.

Authenticate once:
```powershell
& "C:\Program Files\GitHub CLI\gh.exe" auth login
```

## 3. One-Command Publish + Invite
Run the bootstrap script from repo root:
```powershell
.\scripts\github-bootstrap.ps1 -GitHubUsername <your-github-username> -RepoName ece-445-film-digitizer -Visibility private -Collaborator Allannn-sudo
```

This will:
- create the GitHub repository,
- push the local `main` branch,
- invite `Allannn-sudo` as collaborator with push access.

## 4. Manual Commands (Alternative)
```powershell
& "C:\Program Files\GitHub CLI\gh.exe" repo create ece-445-film-digitizer --private --source . --remote origin --push
& "C:\Program Files\GitHub CLI\gh.exe" api -X PUT repos/<your-github-username>/ece-445-film-digitizer/collaborators/Allannn-sudo -f permission=push
```

## 5. Web UI Fallback (No CLI)
1. Create a new private repo on GitHub.
2. In this folder run:
```powershell
git remote add origin https://github.com/<your-github-username>/<repo-name>.git
git branch -M main
git push -u origin main
```
3. Open GitHub repo settings: `Settings` -> `Collaborators and teams`.
4. Click `Add people`, enter `Allannn-sudo`, and send invite.
