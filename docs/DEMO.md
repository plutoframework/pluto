# Pluto: CLI to a working health endpoint

Recorded with the public CLI; temporary paths are normalized to `.`.
The GIF shortens compilation waits. This is the built-in `/healthz` route;
generated business operations still need implementation and authorization.

[Static image](../assets/readme/demo.png) · [Animated demo](../assets/readme/demo.gif)

```console
$ pluto version
pluto 0.13.0
$ pluto --no-input init orbit \
  --module example.com/orbit --env-prefix ORBIT_ \
  --no-docker --no-mise --no-fnox
Created orbit in ./orbit (single tenancy).
Next: go run ./cmd/openapi; pluto fmt; pluto vet; go run ./cmd/api
$ cd orbit

$ ORBIT_SERVER_ADDRESS=127.0.0.1:62046 go run ./cmd/api &

$ curl -fsS -w "\nHTTP %{http_code}\n" http://127.0.0.1:62046/healthz
{"data":"ok"}

HTTP 200
```
