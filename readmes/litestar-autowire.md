# Litestar Autowire

Domain package discovery for Litestar applications.

`litestar-autowire` lets a Litestar app treat each feature or bounded context as
a small Python package. Group the domain's controllers, listeners, and jobs
together, then wire the domain root into the app once.

Use it when your app has a `domains/` package and each domain owns its Litestar
surface:

```text
my_app/
  domains/
    accounts/
      controllers.py
      events.py
      jobs.py
    billing/
      controllers.py
```

## Installation

```bash
pip install litestar-autowire
```

Optional integrations:

```bash
pip install "litestar-autowire[dishka]"
pip install "litestar-autowire[queues]"
```

## Quick Start

Define normal Litestar controllers inside a domain package:

```python
# my_app/domains/accounts/controllers.py
from litestar import Controller, get


class AccountController(Controller):
    path = "/accounts"

    @get("/", sync_to_thread=False)
    def list_accounts(self) -> dict[str, str]:
        return {"status": "ok"}
```

Register the domain root once:

```python
from litestar import Litestar
from litestar_autowire import AutowireConfig, AutowirePlugin

app = Litestar(
    plugins=[
        AutowirePlugin(
            AutowireConfig(domain_packages=["my_app.domains"]),
        )
    ],
)
```

Autowire checks the configured domain package root and its direct child domain
packages for these module names:

- controllers: `controllers`, `routes`, `controller`, `route`
- listeners: `events`, `listeners`
- queue tasks: `jobs`

By default, Autowire defers discovery logs to the Litestar startup lifespan.
The summary includes the number of loaded controllers, domains, listeners, and
tasks. Debug logs include the controller inventory grouped by domain. Set
`log_discovered=False` to disable these logs.

## Integrations

Built-in integrations use string aliases:

```python
AutowireConfig(
    domain_packages=["my_app.domains"],
    integrations=["dishka", "queues"],
)
```

- `dishka`: wrap discovered controllers in Dishka's Litestar router. Configure
  Dishka separately with `setup_dishka(...)`.
- `queues`: import task modules through `litestar_queues.discover_tasks`.
  Configure `QueuePlugin` separately in the Litestar app.

Unknown string aliases raise `ValueError` so typos do not silently disable an
integration.

Use `AutowireLoader` when another registry needs each discovered domain module
loaded:

```python
from litestar_autowire import AutowireConfig, AutowireLoader

config = AutowireConfig(
    domain_packages=["my_app.domains"],
    integrations=[
        AutowireLoader(
            name="inventory_jobs",
            modules="jobs",
            loader="my_app.jobs:discover_jobs",
        )
    ],
)
```

The loader receives existing module paths such as
`my_app.domains.accounts.jobs`. Use the `pkg.module:func` form to make the
module import and callable lookup explicit. If the loader returns an integer,
Autowire adds it to the startup task count.

For custom behavior, pass an integration object:

```python
from litestar_autowire import AutowireConfig, AutowireContext


class InventoryIntegration:
    name = "inventory"

    def on_autowire(self, context: AutowireContext) -> None:
        context.app_config.state["autowire_domain_packages"] = context.config.domain_packages


config = AutowireConfig(
    domain_packages=["my_app.domains"],
    integrations=[InventoryIntegration()],
)
```

Custom integration names cannot reuse built-in names.

## Development

```bash
uv sync --all-extras --dev
make lint
make test
make docs
```
