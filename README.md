# QuBuild

An AI-assisted, interactive platform for learning, designing, simulating and
visualising quantum algorithms — built with Streamlit.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Only `streamlit`, `numpy` and `matplotlib` are required. Qiskit, Cirq and
PennyLane are optional: install any of them and they appear in the Studio's
backend list and genuinely execute your circuits.

---

## What it does

| Module | What is in it |
|---|---|
| **Deliverables** | The problem statement's six objectives mapped to what is built, how each claim is verified, and what is deliberately deferred |
| **Lessons** | 13 lessons across 4 tracks, each with a runnable demo circuit and a check-yourself question |
| **Algorithm library** | 12 canonical circuits — Bell, GHZ, W, phase kickback, Deutsch–Jozsa, Bernstein–Vazirani, Grover (2q and 3q), QFT, teleportation, superdense coding, coin flip |
| **Circuit Studio** | Gate-by-gate builder, live state vector, Bloch spheres, shot histogram, amplitude table and phase wheel, plus a code panel that reads and writes four SDK dialects |
| **Challenges** | 8 problems graded by state fidelity up to global phase, with enforced constraints (banned gates, two-qubit budgets) |
| **Assessment** | 18-question bank across 5 topics, every answer with its reasoning |
| **My progress** | XP, levels, mastery per track, and personalised next steps |
| **Instructor view** | Cohort completion, accuracy distribution, ranked misconceptions, roster |

## Architecture

```
app.py                    >>> YOUR API KEY GOES AT THE TOP <<<  · Streamlit UI, 9 pages
qubuild/
  engine.py               NumPy state-vector simulator (the reference implementation)
  circuits.py             Circuit model, multi-SDK parser, code generators, algorithm library
  backends.py             Qiskit Aer / Cirq / PennyLane adapters, auto-detected
  config.py               the same key settings, if you prefer them out of app.py
  llm.py                  Anthropic / OpenAI / Gemini / Groq / OpenRouter / Ollama adapters
  content.py              Lessons, quiz bank, challenges, tutor knowledge base
  deliverables.py         Delivery table, verification list, phase-2 scope, demo script
  tutor.py                Circuit analysis, optimisation, recommendations, Q&A
  viz.py                  Matplotlib renderers (circuit, Bloch, histogram, dashboards)
  state.py                Learner progress + the demonstration cohort
tests/
  test_physics.py         41 checks on the simulator, the parser and the code generators
  test_backends.py        Every installed SDK must agree with the reference engine
  test_tutor.py           19 checks on the analysis engine, content and challenge solvability
  test_llm.py             18 checks on key handling, request shapes and failure fallback
```

Run everything:

```bash
python run_tests.py
# or, if you have pytest:  pytest tests -q
```

## The simulator

`qubuild/engine.py` is a from-scratch state-vector simulator in NumPy.

* Qubit 0 is the least significant bit and count keys put qubit 0 on the right,
  matching Qiskit. **Cirq and PennyLane order wire 0 first**, so `backends.py`
  reverses their state vectors and bit strings on the way back. This is the
  single most common source of "my results disagree across SDKs".
* 24 gates including `CCX`, `CSWAP`, `CRY`, `CP` and a general `U(θ, φ, λ)`.
* Mid-circuit measurement genuinely collapses the state.
* Bloch vectors come from the single-qubit **reduced density matrix**, so a
  vector shorter than 1 is a real entanglement witness, not a drawing trick.
* Fidelity comparisons are `|⟨a|b⟩|²`, so grading is insensitive to global phase.

`tests/test_physics.py` checks it against every claim the lessons make — Grover
reaching certainty on two qubits and 94.5% on three, Deutsch–Jozsa reporting
"balanced", Bernstein–Vazirani recovering `s = 101`, teleportation actually
moving the state to qubit 2, the QFT of a basis state being uniform, and a Bell
pair leaving both Bloch vectors at the origin.

## Backends

| Backend | Executes on | Needs |
|---|---|---|
| QuBuild · NumPy statevector | built-in engine, exact | — |
| QuBuild · NumPy sampler | built-in engine, shots | — |
| Qiskit Aer · statevector | `AerSimulator` + `qiskit.quantum_info.Statevector` | `qiskit`, `qiskit-aer` |
| Qiskit Aer · qasm sampler | `AerSimulator` with measurement instructions | `qiskit`, `qiskit-aer` |
| Qiskit Aer · noisy device model | Aer `NoiseModel` — depolarising + readout error | `qiskit`, `qiskit-aer` |
| Cirq · Simulator | `cirq.Simulator` | `cirq-core` |
| PennyLane · default.qubit | a real `qml.qnode` | `pennylane` |

These are not labels on one simulator. `backends.py` converts the circuit into
each SDK's native object and runs it there; `tests/test_backends.py` requires
every installed adapter to match the reference state vector to a fidelity
better than 1 − 10⁻⁶ and its sampled counts to within statistical error, on
seven circuits including Grover, the QFT, teleportation and a mixed bag of
parameterised and three-qubit gates.

The noisy backend is a real Aer `NoiseModel`, so the error enters during
simulation rather than being smeared onto the finished histogram.

If an SDK raises at runtime the platform falls back to the built-in engine and
says so in the results panel rather than failing the page.

## The tutor

Two layers, and the split is deliberate.

**The analysis layer** is computed from your actual circuit and your actual
history. It needs no model and no network:

* *Explain circuit* — a gate-by-gate walkthrough with the state after each step,
  including which qubits have become entangled.
* *Check for mistakes* — gates acting after a measurement, idle qubits,
  cancelling gate pairs, zero-angle rotations, mergeable rotations, and an
  excessive two-qubit gate budget.
* *Optimise* — peephole rewrites (`H·Z·H → X`, `H·X·H → Z`, three alternating
  CNOTs → `SWAP`, rotation merging, pair cancellation), each applicable in one
  click. `tests/test_tutor.py` checks that every rewrite preserves the unitary.
* *What next?* — recommendations driven by your weakest quiz topic and your
  position in the syllabus.

**The language layer** answers free-form questions from a curated knowledge
base, and routes them to a real chat model once you add a key. See below.

## Adding your own API key

Open **`app.py`** and fill in the block at the top. It is the first thing in
the file. Save, restart, done:

```python
API_PROVIDER = "anthropic"   # or openai, gemini, groq, openrouter, ollama
API_KEY      = "sk-ant-..."
API_MODEL    = ""            # leave empty for the default
```

(`qubuild/config.py` holds the same settings and still works, if you would
rather keep the key out of `app.py`.)

| PROVIDER | Where the key comes from | Default model |
|---|---|---|
| `anthropic` | console.anthropic.com | `claude-sonnet-4-5` |
| `openai` | platform.openai.com | `gpt-4o-mini` |
| `gemini` | aistudio.google.com | `gemini-2.0-flash` |
| `groq` | console.groq.com | `llama-3.3-70b-versatile` |
| `openrouter` | openrouter.ai | `anthropic/claude-3.5-sonnet` |
| `ollama` | nothing — runs on your machine | `llama3.2` |
| `none` | no key, knowledge base only | — |

The sidebar's **AI tutor · language model** panel shows which provider is live,
and prints the actual error if a key is rejected — so a typo is visible rather
than silent.

**Keeping the key out of the file.** Environment variables win over
`config.py`, which is how you keep a key out of a repo or a deployment:

```bash
set QUBUILD_PROVIDER=anthropic        # Windows
set QUBUILD_API_KEY=sk-ant-...
```

`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY` and
`OPENROUTER_API_KEY` are picked up too, so a key already in your shell works
with no configuration at all.

**A key written into `app.py` is plain text.** Don't commit it to a public
repo and don't deploy the app publicly with a key in it.

**What a key does and does not change.** The analysis layer — walkthrough,
error detection, optimisation rewrites, recommendations — is computed from your
circuit and never leaves your machine, with or without a key. A key only adds
free-form Q&A. And every failure path (no key, wrong key, no network, timeout,
malformed reply) falls back to the knowledge base, so a broken key costs you an
answer, not the app. `tests/test_llm.py` asserts exactly that.

## Multi-SDK code panel

The parser is deliberately tolerant and accepts all of these as the same circuit:

```python
qc.h(0);               qc.cx(0, 1)                 # Qiskit
cirq.H(q[0]);          cirq.CNOT(q[0], q[1])       # Cirq
qml.Hadamard(wires=0); qml.CNOT(wires=[0, 1])      # PennyLane
h q[0];                cx q[0],q[1];               # OpenQASM 2
```

It resolves angle expressions (`pi/2`, `np.pi/4`), reports unknown gates with
line numbers, and skips imports, assignments and print statements. Every
generator round-trips through it in the test suite.

## Data and honesty notes

* **Progress** is a JSON file (`progress.json`, override with
  `QUBUILD_PROGRESS`). `qubuild/state.py` is the seam where a real
  deployment would swap in a learner-record service.
* **The instructor cohort is synthetic** — 24 learners from a fixed seed, so the
  dashboard is stable for a demo. The view says so on screen. Your own row is
  real and comes from the progress file.
* **Exact simulation is capped at 8 qubits.** The state vector doubles with each
  qubit; this is a property of the physics, not a limitation of the code.
