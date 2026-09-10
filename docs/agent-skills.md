# Agent Skills in Studio

How to evaluate, adopt, and use Claude Code Agent Skills in this repository, and the rules
that keep them out of unattended order execution unless we put them there deliberately.

## What a skill actually is

A skill is a directory holding a `SKILL.md` (YAML frontmatter with `name` and `description`,
then a markdown body) plus, optionally, scripts and reference files. The CLI reads every
available skill's `description` and decides on its own when the body is relevant; the body
can then instruct the agent to run the bundled scripts.

Two consequences follow, and both are easy to miss because a skill looks like documentation:

- **A skill is executable.** Bundled scripts run with the agent's permissions. In Studio's
  coding path those permissions are bypassed entirely, so a skill there runs unattended
  with no prompt.
- **The `description` field is an input to the model's control flow.** It is read on every
  session to decide relevance, which makes it a channel for instructions the author wants
  the agent to follow. A description that tries to make itself always-apply
  ("use this before any git operation") is doing something a library's README cannot.

Treat a skill as a dependency, not as configuration.

## Three trust contours

| Contour | What it covers | Where it lives | Loaded by |
| --- | --- | --- | --- |
| 0 — Discovery | Searching the ecosystem, trying candidates | A throwaway `CLAUDE_CONFIG_DIR`, never the default one | An interactive session started with that env var |
| 1 — Development | Skills that help build Studio itself | `.claude/skills/` in this repo, committed | An interactive session in this repo |
| 2 — Workers | Skills used while executing a customer order | `agent-skills/` in this repo, committed | Only via an explicit `--plugin-dir` argument |

Contour 2 is empty by default and must stay that way until a specific skill has earned a
place there. See `docs/local-backend-security-boundary.md` for why the worker loads no
settings sources at all.

## Contour 0 — trying candidates without contaminating anything

The discovery tooling (`npx skills`, `find-skills`) installs into `~/.claude`, which is the
same home directory every other Claude Code invocation on this machine reads. Give evaluation
its own home instead:

```
export CLAUDE_CONFIG_DIR="$HOME/.claude-eval"
```

Two things to know before doing this, both observed directly:

- **Authentication moves with the config directory.** A fresh `CLAUDE_CONFIG_DIR` starts
  logged out and needs its own `claude auth login`.
- **A `SessionStart` hook in that directory runs before the login check.** Code in a config
  directory executes whether or not the session can talk to the API, so "it wasn't logged in"
  is not containment.

Inside that sandbox, install discovery only, and never with `-y` or `--all`:

```
npx skills add github.com/vercel-labs/skills --skill find-skills -a claude-code
```

Then ask for candidates in plain language and read what comes back. Nothing found here is
adopted here; adoption means Contour 1 or 2, which means the audit below.

## The audit, before anything is adopted

Read every file. `claude plugin details <name>` gives the component inventory and the
projected token cost, which is the other half of the decision: this project's pipeline and
interactive sessions draw on one budget, so a skill's resident context cost is a real
operating expense, not a rounding error.

Reject on any of these:

- The `description` claims broad or unconditional applicability, or describes when to run
  rather than what the skill knows.
- The body instructs the agent to fetch and execute remote content, read environment
  variables or credential files, or write outside the working directory.
- A bundled script makes network calls, spawns subprocesses, or touches paths it was not
  given.
- The repository has no history you can read, or the skill is a thin wrapper around
  `curl | sh`.

Weight the code over the author. A recognisable namespace lowers the prior, it does not
replace reading the diff.

## Contour 1 — adopting a skill for Studio development

1. Copy the skill directory into `.claude/skills/<name>/`. A full copy, never a submodule
   or a reference to a branch: the upstream can change after you audit it, and a copy in
   git records exactly what was reviewed.
2. Run `claude plugin validate .claude/skills/<name>` and fix anything it reports.
3. Add a row to `.claude/skills/MANIFEST.md`:

   ```
   | name | source URL | commit SHA vendored from | date | reviewer | why it is here |
   ```

   The SHA is the point of the row. Without it, "we reviewed this" has no referent.
4. Commit the skill and the manifest row in the same commit, so review sees both.

Updating is a dependency bump: fetch the new version, diff it against the vendored copy,
re-audit the diff, update the SHA. A skill update that arrives without a diff has not been
reviewed.

## Contour 2 — letting a worker use a skill

The worker loads no settings sources, so no skill on disk reaches it implicitly — not from
its workspace and not from the operator's own `~/.claude`. The only way in is an explicit
argument, and that is deliberate: the set of skills a run can see should be visible in the
command line that started it and reconstructable from a commit.

To grant one, place the vetted skill under `agent-skills/<stack>/` and pass that directory in
the CLI invocation built in `order_workflow/claude_code_client.py`:

```
--plugin-dir <repo>/agent-skills/<stack>
```

Select the directory with the same static routing that already picks the model per phase — a
function of the order's stack and complexity, not a decision the agent makes about itself.

Verify the combination before relying on it: `--plugin-dir` alongside an empty
`--setting-sources` has not been exercised in this repository, and the interaction between an
explicit plugin path and disabled setting sources should be confirmed by observation, not
assumed. The canary technique in `docs/local-backend-security-boundary.md` works for this too.

Rules for this contour, which hold regardless of how attractive a skill looks:

- No network fetch of skills at runtime. No `npx skills` anywhere under `order_workflow/`.
- No writes into any `.claude/skills/` during an order.
- Nothing enters this contour without having passed Contour 1 first.

## Keeping the discipline

A CI check should fail the build when a directory appears under `.claude/skills/` or
`agent-skills/` without a matching manifest row, and when `npx skills` appears anywhere in
`order_workflow/`. The rules above are worth exactly as much as their enforcement: they will
be followed until the first deadline that makes following them inconvenient.

## Worth writing before importing

Studio encodes a lot that no community skill knows: the order workflow, phase complexity
routing, the Docker QA runner, Elena's style library, the UI vocabulary. A generic skill for a
popular framework will restate less than Studio already knows and will bring assumptions that
conflict with it.

The transferable idea from this ecosystem is the packaging format, not its catalogue. The
highest-value skills for this repository are likely to be ones written here, for tasks that
are currently done by hand every week — running a live order end to end and collecting the
audit bundle, vendoring and auditing a candidate skill, diagnosing a failed QA container.
Those carry no supply-chain risk at all.
