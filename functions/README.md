# Power Mgmt Function app

This function app is responsible for power management functionality in azure.

You can run it locally after you have installed the following dependencies:

* Azure CLI
* Azure Function Tools
* python3 and pip3

## Up and running

- Copy the file `local.settings.json.sample` to `local.settings.json`, and update values in there accordingly.

- Create a virtual environment (once off).

```
python3 -m venv .venv
```

- Install dependencies

```
.venv/bin/python -m pip install -r requirements.txt
```

- Run it!

```
. .venv/bin/activate && func host start
```

## Up and running in vscode

Often, opening this project in vscode will cause it to detect the presence of a function app,
and add a debug option 'attach to python functions'. which is a nice way to debug.