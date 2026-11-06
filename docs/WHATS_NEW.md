# What's New: Related Fix Discovery

This branch (`nodes2026-enhancements`) adds four things to the Stack Trace → Fix Path app: Related Fix
Discovery, an evidence-edges inspector, a version-applicability showcase, and a professional UI theme. The
original Summary/Fix Graph/Code Diff/Evidence/Cypher tabs, the no-match behavior, and the retrieval ranking
logic are unchanged.

Live doc version of this page: https://claude.ai/artifact/7VK7LGsQzghk7N8RbzxJNY

## 1. Related Fix Discovery (new "Related Fixes" tab)

After investigating an error and landing on a historical fix, a new tab answers "what else in this
codebase's history looks related?" using real Neo4j graph traversal, not an LLM guessing.

- Finds other PRs that share an **exception type**, a **changed file**, or a **function name** with the fix
  being viewed.
- Each result shows the PR number and title (linked), a 0–100 relatedness score, the specific reasons it
  matched (for example "changed-line match: InvalidURL, UnicodeError"), and its earliest release if known.
- Every result is labeled **"Related candidate — NOT a verified fix"** so it can never be mistaken for the
  actual answer.
- Filter buttons narrow results by match type: shared exception, file, or function.
- An **"Investigate PR #N separately →"** button runs a brand-new investigation anchored on that related PR.
- An honest empty state explains when there is nothing to show, rather than faking a result.

For the headline demo case (PR #5414), it surfaces **PR #3789 "Lazy load idna library"** as the top lead — a
real, independently-documented ambiguity in this codebase's history, not a seeded example.

## 2. Evidence-edges inspector (new, under "Fix Graph")

Below the existing graph diagram, an expandable table now answers "why does this arrow exist?" for every
edge in the fix path — not just the issue-to-PR link that was already shown, but also:

- The PR's merge commit, with its verification basis and source link.
- Every release that contains that commit, with git-tag-ancestry provenance.
- Changed files — honestly labeled "structural (git diff)" with "Not available in this snapshot" for fields
  this data simply doesn't have, rather than inventing them.

## 3. Version-applicability showcase (new, on the main result)

Right under the match metrics, a prominent callout now states plainly whether the installed version already
has the fix or needs an upgrade — plus two one-click buttons, **"Show for installed 2.26.0"** and **"Show
for installed 2.31.0"**, that instantly re-run the same query against each version so the advice can be seen
flipping live.

## 4. Professional UI theme

Not new functionality, but a visible change: custom indigo branding, a cleaner sidebar, and removal of the
default "Deploy" dev-tool chrome Streamlit shows by default.
