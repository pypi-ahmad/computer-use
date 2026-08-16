# Disclaimer

Read this before you run a task. It is the short version; the linked files
have the details.

## No warranty

This software is MIT-licensed and provided **as is**, with no warranty of
any kind. See [LICENSE](LICENSE). The maintainer is not liable for damages,
data loss, or provider charges arising from running this workbench.

## You run it, you own it

Computer Use Workbench is not a hosted service. You clone it, run it on
your own machine, and use your own API keys. The maintainer never receives
your files, credentials, or task text. See [OPEN_SOURCE.md](OPEN_SOURCE.md).

## Your data is your responsibility

Everything you put into a session — uploaded files, sandbox contents,
browser pages the agent opens, screenshots, SQLite history, `.env`
secrets — is **yours to manage**. This repo does not review it, back it
up, or make it safe. Full breakdown, including what leaves your machine
and goes to a provider: [DATA.md](DATA.md).

## Computer Use is not risk-free

The model can click, type, submit forms, and spend money in a browser
session. Use test accounts and non-sensitive data. Running the sandbox
in Docker limits *where* actions land; it does not make the task safe or
review what the model decides to do. See the warning at the top of
[README.md](README.md) and [docker/SECURITY_NOTES.md](docker/SECURITY_NOTES.md).

## Provider terms are yours to accept

OpenAI, Anthropic, and Google own their Computer Use APIs and models.
This project is an independent local operator surface, not an official
vendor product, and does not speak for any provider's terms, pricing,
or availability. Session cost figures in the app are list-rate estimates
on recorded tokens, not an invoice — see the Session cost section of
[README.md](README.md).

## No financial support wanted

This project does not want or accept donations, sponsorship, bounties,
or paid support of any kind. Testing, bug reports, ideas, and pull
requests are the help that matters — see [SUPPORT.md](SUPPORT.md) and
[CONTRIBUTING.md](CONTRIBUTING.md).

## Security and vulnerabilities

Do not use this disclaimer to report a vulnerability. Follow
[SECURITY.md](SECURITY.md) instead.
