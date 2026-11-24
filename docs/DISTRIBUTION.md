# A copy for an organisation

The public repository has no data of any organisation. An organisation's copy is a second repository with one extra
commit on top; changes only flow down.

```
public repository  --pull-->  the maintainer's laptop  --push-->  the organisation's repository  -->  users
                              + setup/site.json
```

`setup/site.json` exists only in the organisation's repository:

```json
{"update": {"remote": "https://github.com/ORGANISATION/powertools.git", "branch": "main"}}
```

It decides where new ZIP copies are attached to, so they take updates from there. Ideas go to the issues of the
repository the copy follows.

## First time

```
git clone https://github.com/brudnypiotr/powertools.git powertools-org
cd powertools-org
git remote rename origin public
git remote add origin https://github.com/ORGANISATION/powertools.git
rem write setup/site.json, then:
git add setup/site.json
git commit -m "chore(site): update source"
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
