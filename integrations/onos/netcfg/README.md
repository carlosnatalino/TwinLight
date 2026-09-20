# netcfg — registering the twin as an ODTN device

`twinlight-ols.json` is pushed to ONOS by
`integrations/onos/scripts/demo-up.sh`. Run from the repository root:

```bash
curl -u onos:rocks -X POST -H 'Content-Type: application/json' \
     -d @integrations/onos/netcfg/twinlight-ols.json \
     http://localhost:8181/onos/v1/network/configuration
```

The file is kept free of comments deliberately. ONOS validates every top-level
key against a registered `SubjectFactory`, so an explanatory `"_comment"` block
is not ignored — it comes back as:

```
HTTP 207 {"code":207,"message":["subjectClassKey '_comment' not found"]}
```

which is a partial-success code, so a naive script will not even notice. Hence
this file instead.

## The fields that matter

**`"rest:172.28.0.10:8282"`** — the device key. ONOS parses the `DeviceId`
straight out of it, so it must be `rest:<ip>:<port>` and must agree with the
`rest` block below. The address is the static IP the adapter is assigned in
`../docker-compose.onos.yml`; change one and you must change the other. It has
to be a literal IP, not the `tapi-adapter` compose service name, because
`RestDeviceConfig.ip()` parses the value as an `IpAddress`.

**`"driver": "ols"`** — the driver that carries the T-API behaviours
(`TapiDeviceDescriptionDiscovery`, `TapiDeviceLambdaQuery`,
`TapiFlowRuleProgrammable`) in ONOS's `odtn-driver` bundle. It is declared in
`drivers/odtn-driver/src/main/resources/odtn-drivers.xml` as
`manufacturer="tapi-swagger" swVersion="2.1"`. There is no driver called `tapi`
and no `org.onosproject.drivers.tapi` app — advice to activate one is wrong.

**`"testUrl"`** — the reachability probe used by the REST device provider.

## The field that must stay absent

There is deliberately **no `"url"` key**. ONOS treats it as a *prefix* prepended
to every request path:

```java
// RestSBControllerImpl.getUrlString
if (device.url() != null && !device.url().isEmpty()) {
    return device.protocol() + "://" + device.ip() + ":" + device.port()
            + device.url() + request;
}
```

So setting `"url": "/restconf/data/tapi-common:context"` — as several ODTN
examples appear to — makes the driver request
`/restconf/data/tapi-common:context/restconf/data/tapi-common:context`, and
discovery fails with a 404 that never mentions the cause. Use `testUrl` for the
probe and leave `url` out.

## Removing the device

```bash
curl -u onos:rocks -X DELETE \
     'http://localhost:8181/onos/v1/network/configuration/devices/rest:172.28.0.10:8282'
```
