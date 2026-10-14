# EltePortal

EltePortal is a toolbox for ELTE students who want to use the university systems they already depend on without living in a pile of browser tabs. It reads Canvas, the bundled ELTE Neptun profile, the public timetable, and selected public course-file sites, then gives you the result in one consistent shape.

It is for the student who wants to find a file, check what is due, search a timetable, or keep a local record of a semester. You can use it as a normal terminal command. If you write scripts, the same toolbox also gives you clean JSON and a small Python library.

## Why use it?

ELTE has more than one place where your academic life happens. Canvas has course pages and files. Neptun holds another part of your student information. Tanrend has the public schedule. A department site may have the one PDF you actually need. None of this is complicated by itself, but moving between all of it gets old fast.

EltePortal puts the read side of those systems behind commands that behave the same way. You can ask for your Canvas courses, download the files from one course, search the timetable, inspect named Neptun records after logging in, or write local reports about missing work and grades. It does not pretend to replace the websites. It makes the repetitive parts less annoying.

It also refuses to be clever with your account. A bad automatic retry can put a captcha or lockout in your way. Regenerating a calendar-export link can break every calendar that uses it. So the tool stops instead of guessing. That is not a missing feature. It is the point.

## What you can do with it

| If you need to... | Use EltePortal to... |
| --- | --- |
| See the courses Canvas knows about | List active, completed, and pending Canvas enrolments. |
| Get course material onto your machine | List or download files from a Canvas course into a folder you choose. |
| Check what is coming up | Read Canvas upcoming events and assignment dates. |
| Keep a local academic record | Archive Canvas course data and write local missing-work or grade reports. |
| Search the timetable | Look up a subject code, teacher, or subject name in the public timetable. |
| Read Neptun data | Log in once, keep a short-lived session locally, then call named read endpoints from the selected profile. |
| Find public teaching material | Browse configured public file servers and check whether configured student sites answer. |

You do not need to use every part. A Canvas-only setup is useful on its own. A profile decides which systems belong to your institution.

## Install and run a first command

From a source checkout, in PowerShell on Linux or macOS:

```powershell
python3 -m venv .venv
& ./.venv/bin/python -m pip install .
& ./.venv/bin/elte --json config check
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install .
& ./.venv/Scripts/elte.exe --json config check
```

The check validates the bundled ELTE profile without contacting a university service or reading a credential. Python 3.11 or newer is required. Package index publication is pending, so install from source for now.

After you configure a Canvas token, these are the sort of commands you will use:

```powershell
& ./.venv/bin/elte --json canvas courses
& ./.venv/bin/elte --json canvas files <COURSE-ID>
& ./.venv/bin/elte --json canvas pull <COURSE-ID> --into <FOLDER>
& ./.venv/bin/elte --json tanrend <SUBJECT-OR-TEACHER> --term <TERM>
```

`--json` is useful for scripts and gives every result the same envelope. Without it, the command prints something intended for a human. Start with `config check`, then configure only the systems you actually use.

## Supported systems and profiles

| Provider | What you can read | What a profile supplies |
| --- | --- | --- |
| Canvas | Courses, assignments, files, upcoming items, and archive data | The instance's HTTPS `/api/v1` base URL |
| Neptun | Named read endpoints through a cached student session | Portal and student API URLs, locale, endpoint paths, and an explicit flow |
| Timetable | Searchable public HTML timetable | Search page URL; the built in parser expects the ELTE table layout |
| Open files | An Apache style directory and a static course site | Server URLs and optional site entries |

The bundled [ELTE profile](elteportal/profiles/elte.toml) is a reference. To add another institution, copy [the minimal example](examples/university.toml), edit its URLs and enabled providers, then run `elte --profile <PATH-TO-PROFILE> --json config check`. This requires no Python for Canvas or sites with the supported formats. A different timetable layout or Neptun login flow needs a provider plugin; a profile must say `flow = "unsupported"` until that implementation is tested. The ELTE Neptun flow is preserved from local observations and has not been publicly verified at other universities. See the [profile reference](docs/profiles.md) and [Neptun notes](docs/neptun.md).

## Everything it can do

Every command below takes `--json`. Canvas commands need `CANVAS_API_KEY`, Neptun commands need a cached login, and the timetable, the open file servers and `sites` need nothing at all.

### Canvas

Canvas access is GET only. The single exception is `canvas nickname`, which does nothing until you add `--apply`.

| Command | What you get |
| --- | --- |
| `elte canvas courses [--term TEXT]` | Every course the account can see, concluded ones included, once each, newest term first: id, code, name, original name, start date. `--term` matches a substring of the name, the original name or the course code, so a nicknamed course still answers to its term. |
| `elte canvas files <COURSE>` | The course's files with id, name and size, plus a total. Signed download links are redacted in JSON and the command says so in a warning. |
| `elte canvas pull <COURSE> --into <DIR>` | Downloads the course's files. Files already in the folder are skipped unless you add `--force`. `--skip-images` leaves out png, jpg and jpeg. |
| `elte canvas links [--course <ID>]` | Every non-Canvas URL a course links out to, found in the syllabus, pages, assignment descriptions, announcements and external-URL module items, each with where it was found, and a count per host. Leave out `--course` to sweep every course. This is how you find the Panopto recordings and department sites that nothing advertises. |
| `elte canvas upcoming` | Upcoming events for the account: title, date, link. |
| `elte canvas archive --account <ACCOUNT> --into <DIR> [--workers N]` | A local copy of the whole account, four courses at a time by default. Per course it writes the course record, files (downloaded), people, modules, external tools, discussions with each thread, pages with each page body, assignments with your submissions, quizzes with their questions and your submissions, and an `errors.json` for whatever Canvas refused. Per account it writes your profile, enrolments, and the inbox, sent and archived conversations. Reading the inbox does not mark anything as read. It writes `archive-status.json` as it goes and skips files it already has, so a second run picks up where the first stopped. |
| `elte canvas nickname --archive <DIR> [--apply]` | Compares your Canvas course nicknames with English, term-qualified ones derived from an archive. With `--apply` it sets them. This is the only write the tool can make. |
| `elte reports missing` | Writes `Canvas - Missing Work.md`: everything not yet handed in across your active courses, split into overdue and still due, with what each item is worth. |
| `elte reports grades` | Writes `Canvas - Grades.md`: your running score per course, with the current score (graded work only) next to the final score (ungraded work counted as zero). It never turns a percentage into a 1 to 5 grade, because Canvas carries no such mapping. |

Both reports rewrite their file on every run, in `$env:ELTE_REPORT_DIR` or `~/.local/share/elteportal/reports`.

### Neptun

The tool reads exactly the 35 endpoints named in `[neptun.endpoints]` in `elteportal/profiles/elte.toml`. You call each one with `elte neptun get <name>`, and it sends a GET request with your cached login token. If the server answers 401 the token is refreshed once and the call repeated. A second failure stops there.

| Command | What it does |
| --- | --- |
| `elte neptun creds` | Checks that your username, password and TOTP seed resolve, and whether a token is cached. No request goes to Neptun. |
| `elte neptun code` | Checks that a TOTP code can be generated and how many seconds it has left. The code is never printed. |
| `elte neptun probe` | Asks every endpoint whether it exists, without credentials. A 401 or a redirect to the login page counts as "exists, wants auth". |
| `elte neptun login` | Logs in once through the portal and caches the token in a 0600 file. `--code` overrides the TOTP code, `--guid` accepts a hand-off GUID or the whole `outerlogin?GUID=` URL copied from a browser, `--wait <SECONDS>` keeps asking for the hand-off while the student web reports itself full (no extra login attempts), and `--api` tries the API's own login, which ELTE answers with a redirect. |
| `elte neptun logout` | Drops the cached token. |
| `elte neptun get <name> [--param KEY=VALUE]` | Reads one of the endpoints below. `--param` is repeatable and overrides the paging defaults the list endpoints (exam results, invoices, scholarships, publications and a few more) ask for. |

| Name | Neptun API path | What it is |
| --- | --- | --- |
| `environment` | `General/EnvironmentData` | Portal environment data |
| `userinfo` | `UserInfo` | Your user and student info |
| `trainings` | `MyTrainings` | Your programmes |
| `calendar_events` | `Calendar/GetCalendarEvents` | Calendar events |
| `calendar_trainings` | `Calendar/GetStudentTrainings` | Programmes, for the calendar view |
| `calendar_links` | `Calendar/GetLinksForCalendarExport` | Calendar export links (fetched, but the output is redacted) |
| `course_terms` | `SubjectCourse/GetTerms` | Terms you can browse courses for |
| `courses` | `SubjectCourse/GetSubjectCourseList` | Subject and course list |
| `averages` | `Advancement/GetTermAveragesByTraining` | Grade averages per term |
| `credits` | `advancement/creditprogress` | Credit progress |
| `curriculum_templates` | `Advancement/GetStudentCurriculumTemplates` | Curriculum templates |
| `completable_subjects` | `Curriculum/GetCompletableCurriculumSubjects` | Subjects you can still complete |
| `optional_summary` | `Curriculum/GetOptionalSubjectsSummary` | Elective subjects summary |
| `semesters` | `administration/semiannualregistration/semesters` | Semester registration status |
| `exam_terms` | `ExamResults/GetTermsForGetExamResultsList` | Terms that have exam results |
| `exam_results` | `ExamResults/GetExamResultsList` | Exam results |
| `exams_registered` | `ExamRegisteredExams/GetRegisteredExamsList` | Exams you are registered for |
| `exams_available` | `ExamRegistration/GetExamsList` | Exams open for registration |
| `invoices` | `Invoices/GetInvoicesForStudent` | Invoices |
| `collective_invoices` | `FinancialDataDashboard/GetCollectiveInvoices` | Collective invoices |
| `to_pay` | `FinancialItem/GetItemsToBePayed` | Items still to pay |
| `scholarships` | `Scholarship/GetScholarshipPayments` | Scholarship payments |
| `scholarship_terms` | `Scholarship/GetScholarshipAvailablePaymentTerms` | Scholarship payment terms |
| `dormitory` | `Dormitory/GetDormitoryDashboardPeriodsData` | Dormitory periods |
| `messages_mandatory` | `Messages/Mandatories` | Messages you must read |
| `unread_count` | `Message/GetUnreadedMessagesCount` | Unread message count |
| `tasks` | `Tasks/GetDashboardTasksData` | Dashboard tasks |
| `requests` | `RequestForm/GetNumberOfRequestForms` | Number of request forms |
| `publications` | `Publication/GetPublications` | Publications |
| `queries` | `Queries/GetQueriesListData` | Queries list |
| `fir` | `FIR/GetStudentFIRData` | Your FIR data |
| `taken_terms` | `TakenSubjects/Terms` | Terms with taken subjects |
| `taken_subjects` | `TakenSubjects/GetTakenSubjects` | Subjects you have taken |
| `registry_terms` | `RegistrySheet/GetStudentTrainingTerms` | Terms on your registry sheet |
| `grades` | `RegistrySheet/GetStudentTakenSubjectsByTerm` | Grades per term, from the registry sheet (needs `--param studentTrainingTermDataId=<ID>`, the id comes from `averages`) |

Three more entries in the same table are only used for logging in: `authenticate`, `refresh` and `outer_login`. The tool never calls Neptun's calendar export link regeneration endpoint, because it would break every calendar app subscribed to the old link.

### Timetable

`elte tanrend <QUERY> --term <TERM> [--mode code|subject|teacher]` searches the public ELTE timetable without a login. The term can be written `2026/27/1` or `2026-2027-1`. You get one row per timetable line, with the day, time, room, teacher and the weeks it runs. The timetable does not know how full a group is. Headcount and ranking exist only in Neptun.

### Open course files

| Command | What you get |
| --- | --- |
| `elte wornox ls [--match TEXT] [--root URL]` | Every file on the open course file server, walked five levels deep, optionally filtered by a substring of the path. |
| `elte wornox pull <MATCH> --into <DIR>` | Downloads every file whose path contains the text. |
| `elte webprog lectures` | The web programming lecture decks that answer, numbered, with their titles and a link that prints each one to PDF. This is the default. |
| `elte webprog subjects` and `elte webprog materials` | The department site's subject list and materials list, with their links. |

### Checks and plugins

| Command | What you get |
| --- | --- |
| `elte sites [--only KEY ...]` | Whether each ELTE system in the profile answers, its status, and what kind of login it wants. |
| `elte config check` | A check of your config and profile with no network call and no credential read. |
| `elte provider <NAME> <ACTION> [--param KEY=VALUE]` | Calls a built-in or installed provider directly. The built-in Canvas provider answers `courses`, `files`, `assignments` and `upcoming`. `assignments` returns every assignment together with your submission, which has no `elte canvas` command of its own. |

## Configuration

EltePortal reads `$env:ELTE_CONFIG` if set, otherwise `$env:XDG_CONFIG_HOME/elteportal/config.toml`, otherwise `~/.config/elteportal/config.toml` when present. An absent config uses the bundled ELTE profile and environment secrets. A minimal config looks like this:

```toml
profile = "/absolute/path/to/university.toml"

[secrets]
backend = "env"
```

Pass `--config <PATH>` or `--profile <PATH>` before the subcommand for one invocation. `$env:ELTE_PROFILE` overrides the profile path; `$env:CANVAS_BASE_URL`, `$env:NEPTUN_BASE_URL`, and `$env:NEPTUN_PORTAL_URL` override their profile URLs and are checked for HTTPS. `elte --json config check` reports the selected profile and backend, never their secret values. The full field list is in [configuration and profile reference](docs/profiles.md).

## Secrets

Set `$env:CANVAS_API_KEY` for Canvas. Neptun uses `$env:NEPTUN_USER`, `$env:NEPTUN_PASS`, and, for TOTP accounts, `$env:NEPTUN_TOTP_SECRET`. Environment values win over the configured backend. Blank or whitespace only values are refused.

| Backend | Where the secret lives | Setup |
| --- | --- | --- |
| `env` | In the process environment | Default; supply the variables yourself. |
| `keyring` | In your OS keyring | Install `.[keyring]`; store each variable name under service `elteportal`. |
| `file` | In a JSON file you choose | Point `secrets.file` at a regular file owned by you with mode 0600; it is cleartext at rest. |
| `command` | In your password manager | Set each secret name to an argument array under `[secrets.commands]`; stdout is the value, stderr is never shown. |
| `legacy` | In an existing optional vault | Explicitly select this backend; it is for existing setups, not required for a new install. |

The command backend lets you use a manager CLI without putting a command, account name, or secret in the project code. [Secrets guide](docs/secrets.md) gives the exact TOML shapes. `elte --json neptun creds` checks resolution without a login and reports only whether each required value resolved.

## Safety rules

Canvas reads use GET. The sole Canvas write is a course nickname change, and `elte canvas nickname --archive <PATH>` only shows a comparison until you add `--apply`. That matters because a course nickname changes what Canvas displays elsewhere. Credentials are checked before a Neptun login request; an empty password is a local error, not an experiment on your account. Failed authentication is never retried automatically because repeated mistakes can trigger a captcha or lockout. A cached, known expired token may be refreshed once after an authenticated 401. The client never calls Neptun's calendar export link regeneration endpoint because it invalidates existing subscriptions. Signed Canvas file links are redacted in JSON output because the link itself can grant a download. See [safety details](docs/safety.md).

## Output and exit codes

Every successful `--json` result has this versioned envelope:

```json
{"schema_version":"1.0","ok":true,"command":"config check","data":{"profile":"ELTE","schema_version":1,"canvas":true,"neptun_flow":"elte-portal","secrets_backend":"env"},"warnings":[]}
```

Failures add `"error"` and set `"ok":false`. Exit code 0 means success, 1 means a runtime or configuration failure, and 2 means invalid command line arguments. The [JSON Schema](elteportal/schemas/envelope-v1.json) defines this shape. Data fields vary by command; compare parsed JSON, not terminal spacing.

## Use it from Python

The CLI uses the same profile bound client. `Client.call()` returns an envelope and masks unexpected plugin exceptions; `Client.data()` returns the provider's value and lets normal exceptions propagate.

```python
from elteportal import Client

client = Client(profile_path="examples/university.toml")
result = client.call("config", "check")
print(result["ok"], result["data"]["profile"])
```

This example is offline and prints `True Example University`. The older `elteportal.canvas`, `elteportal.nicknames`, and `python -m elteportal.reports` imports remain available through at least one minor release.

## Add a provider or secret backend

A provider package registers `elteportal.providers` entry point to a class with `run(action, **params)`. A secret backend registers `elteportal.secrets` to a callable accepting `(env_name, settings)`. Both are loaded only when selected. A plugin must not return raw credentials or add an implicit write; [the plugin guide](docs/plugins.md) has a minimal working package and an offline test.

## What this is not

It is not a universal Neptun client, a scheduler, a grade authority, or permission to scrape an institution. It will not submit assignments, enroll in courses, bypass MFA, solve captchas, or regenerate calendar links. A parser cannot make an undocumented deployment portable by changing a hostname. Nice try, hostname.

## FAQ and troubleshooting

**Can I use an OAuth login instead of a Canvas token?** Canvas documents OAuth2, but this client currently accepts an access token that you supply; it does not register an OAuth app. Use only a token you are authorized to create.

**Why is a Neptun login refused?** Run `elte --json config check`, then `elte --json neptun creds`. A missing value, unsupported flow, or spent TOTP code is a reason to stop and inspect the account in a browser. The client will not retry for you.

**Why are some Canvas courses or files absent?** Canvas permissions and publication state determine what its API returns. A concluded enrollment may still be visible, while an unpublished course can deny its files. Check the course in Canvas before treating the client result as a complete archive.

**Where do local reports go?** By default, under your user data directory in `elteportal/reports`. Set `$env:ELTE_REPORT_DIR` to choose another location.

## Responsible use

[Instructure's API policy](https://www.instructure.com/policies/canvas-api-policy) and [throttling documentation](https://developerdocs.instructure.com/services/canvas/basics/file.throttling) apply to Canvas access. No ELTE specific automated access allowance or fixed Neptun rate limit was verified for this release. Ask your institution before automating repeated collection, keep requests modest, and stop on authentication failure. This tool never claims that an available endpoint grants permission to use it.

## Contributing and licence

Start with [CONTRIBUTING.md](CONTRIBUTING.md), use invented offline fixtures, and report security issues privately using [SECURITY.md](SECURITY.md). EltePortal is licensed under [Apache-2.0](LICENSE), with the accompanying [NOTICE](NOTICE).
