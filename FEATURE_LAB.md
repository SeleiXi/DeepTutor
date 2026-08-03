# DeepTutor Feature Lab

Feature Lab runs the eight independent SeleiXi feature branches at the same time.
Each variant gets its own detached Git worktree, runtime data, ports, Next.js
cache, log, and recorded launcher process.

## One-command launch

On Windows, double-click `feature-lab.cmd`, or run:

```powershell
.\feature-lab.ps1
```

That command fetches the branches from `origin`, prepares their worktrees,
starts all eight variants in the background, waits for their backend and frontend
health checks, and prints every URL.

Use the same entry point for lifecycle commands:

```powershell
.\feature-lab.ps1 status
.\feature-lab.ps1 stop
.\feature-lab.ps1 start guided-question teacher-techniques --open
.\feature-lab.ps1 stop guided-question teacher-techniques
```

The portable Python entry point has the same interface:

```bash
python scripts/feature_lab.py start
python scripts/feature_lab.py status
python scripts/feature_lab.py stop
```

## Variants and ports

| Variant key | Branch | API | Web |
|---|---|---:|---:|
| `answer-feedback` | `feat/answer-effectiveness-feedback` | 8811 | [3811](http://localhost:3811) |
| `diagnostic-follow-up` | `feat/diagnostic-follow-up` | 8812 | [3812](http://localhost:3812) |
| `memory-reconciliation` | `feat/memory-evidence-reconciliation` | 8813 | [3813](http://localhost:3813) |
| `teacher-techniques` | `feat/teacher-exam-techniques` | 8814 | [3814](http://localhost:3814) |
| `guided-question` | `feat/ask-questions-capability` | 8815 | [3815](http://localhost:3815) |
| `chat-parent-fix` | `feat/fix-stale-chat-parent` | 8816 | [3816](http://localhost:3816) |
| `codebuddy` | `feat/codebuddy-provider` | 8817 | [3817](http://localhost:3817) |
| `antigravity` | `feat/antigravity-support` | 8818 | [3818](http://localhost:3818) |

Pass a variant key, the full branch name, or omit variants to target all eight.

## Isolation and local configuration

The default lab directory is a sibling of this checkout named
`DeepTutor-feature-lab`. Its layout is:

```text
DeepTutor-feature-lab/
  worktrees/<variant>/   detached source checkout
  runtime/<variant>/     isolated DEEPTUTOR_HOME and data
  logs/<variant>.log     combined launcher/backend/frontend output
  state/<variant>.json   verified process identity and assigned ports
```

On a variant's first start, Feature Lab copies `data/user/settings` from this
checkout so configured model providers are immediately available. It never
copies chat history, memory, skills, or knowledge bases, so experiments cannot
contaminate one another. Later starts preserve the variant's own settings.
Use `--no-seed-settings` for a completely fresh configuration, or
`--seed-home PATH` to seed from a different DeepTutor workspace.

The launcher explicitly pins each process's `PYTHONPATH` to its detached
worktree. This is required when the development interpreter has another
DeepTutor checkout installed in editable mode.

## Safety and requirements

- Python 3.11+, the DeepTutor backend dependencies, Git, Node.js, and npm are
  required.
- Each worktree installs its own `web/node_modules` from the committed lockfile.
  npm's download cache keeps repeat setup reasonably fast. The directories are
  intentionally not linked because Next.js Turbopack rejects dependencies that
  resolve outside a worktree's filesystem root.
- Startup aborts before launching if a required port belongs to another
  process.
- `stop` terminates only a PID whose live command still matches the recorded
  Feature Lab script and runtime home. A recycled or unrelated PID is left
  untouched.
- Existing worktrees update only when clean. Local edits are never overwritten.

Set `DEEPTUTOR_PYTHON` before invoking `feature-lab.ps1` if DeepTutor uses a
Python interpreter that cannot be discovered automatically.
