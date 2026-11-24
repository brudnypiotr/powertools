# A copy for an organisation

The public repository has no data of any organisation. An organisation's copy is a second repository with one extra
commit on top (update source and look); changes only flow down.

```
public repository  --pull-->  the maintainer's laptop  --push-->  the organisation's repository  -->  users
                              + setup/site.json, branding/
```

## The update source

`setup/site.json` exists only in the organisation's repository:

```json
{"update": {"remote": "https://github.com/ORGANISATION/powertools.git", "branch": "main"}}
```

It decides where new ZIP copies are attached to, so they take updates from there. Ideas go to the issues of the
repository the copy follows.

## The look

Name, accent and sidebar colours, logo and header background live in `branding/` (git-ignored here, so it never gets into
the public repository):

```
branding/branding.json   {"powertools_name": "...", "colors": {"accent": "#3366cc", "sidebar": "#101828", "sidebar_active": "#1d2939"}}
branding/logo.png        (or .svg, .jpg)
branding/background.png  (optional, behind the logo)
```

Try it in Settings > Look: import a `branding.zip` (one made for another app works: its logo and background are used, its
name is not), pick the colours, Apply, then **Make branding.zip**. Unpack it in the organisation's repository and
`git add -f branding`: everyone gets it with the next Update. What someone sets in Settings stays on their laptop and wins.
Colours are corrected where text would be hard to read. The red dot stays the icon.

## First time

```
git clone https://github.com/brudnypiotr/powertools.git powertools-org
cd powertools-org
git remote rename origin public
git remote add origin https://github.com/ORGANISATION/powertools.git
rem write setup/site.json and unpack branding.zip, then:
git add setup/site.json
git add -f branding
git commit -m "chore(site): update source and look"
git push -u origin main
```

## Every release

```
git fetch public
git merge public/main
git push
```

A merge, not a rebase: existing copies then update with an ordinary fast-forward.

## Copies that follow the public repository

About (version, bottom left) > "Take updates from another repository" > the new address > Switch. It is refused when that
repository does not contain the version the copy has, so merge the newest public version first. A private repository asks for
the GitHub sign-in in the browser (Git Credential Manager is part of Portable Git).
