# GitHub Setup

This guide takes the local project in this folder and publishes it to GitHub.

## 1. Initialize Git Locally
```powershell
git init -b main
git add .
git commit -m "Initial project scaffold"
```

## 2. Create GitHub Repo (Preferred: GitHub CLI)
Install `gh` first if needed:
```powershell
winget install --id GitHub.cli -e --source winget
```

Then authenticate and create repo:
```powershell
gh auth login
gh repo create ece-445-film-digitizer --private --source . --remote origin --push
```

Replace `ece-445-film-digitizer` with your preferred name.

## 3. Invite Your Group Member
After repo creation, invite collaborator `Allannn-sudo`:
```powershell
gh api -X PUT repos/<your-github-username>/ece-445-film-digitizer/collaborators/Allannn-sudo -f permission=push
```

Use your real username and repo name in the command.

## 4. Web UI Fallback (No CLI)
1. Create a new private repo on GitHub.
2. In this folder run:
```powershell
git remote add origin https://github.com/<your-github-username>/<repo-name>.git
git branch -M main
git push -u origin main
```
3. Open GitHub repo settings: `Settings` -> `Collaborators and teams`.
4. Click `Add people`, enter `Allannn-sudo`, and send invite.
