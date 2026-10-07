# Provider and secret plugins

Provider plugins are separate Python distributions registered under the `elteportal.providers` entry point group. The entry point points to a class with a no argument constructor and a `run(action: str, **params)` method. Return JSON compatible data. The CLI wraps it in the same envelope as the built in providers; `Client.call()` masks unexpected exception text. A plugin must validate its parameters and keep credentials out of data and logs.

The runnable [sample provider](../examples/sample-provider) is a separate package. Install it into the same environment as EltePortal, then run `elte --json provider example ping --param institution=Example`. It returns `{"reply":"pong","institution":"Example"}` inside `data`. The plugin has no network access and is suitable for testing discovery. Its entry point declaration is:

```toml
[project.entry-points."elteportal.providers"]
example = "example_provider:ExampleProvider"
```

The built in providers implement the same `run` method. Their actions are `canvas courses|files|assignments|upcoming`, `neptun probe|creds|login|logout|get`, `timetable search`, `files walk`, `sites probe`, and `config check`. The more specialized legacy CLI commands remain available while integrations migrate. Do not add a write action that runs from a read sounding command.

A secret backend registers a callable under `elteportal.secrets`. The callable receives `(env_name, settings)` and returns a nonempty string. The resolver checks the environment first, then calls the selected backend; exceptions are masked before user output. A package can publish this entry point:

```toml
[project.entry-points."elteportal.secrets"]
example = "example_secrets:read_secret"
```

No third party backend should be installed merely to discover it. Install only a plugin whose code you trust, since an entry point executes in your process with access to your local account.
