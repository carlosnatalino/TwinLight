/**
 * A twin's /.well-known/host-meta for tests that mock fetch.
 *
 * The API client discovers the RESTCONF root before its first T-API request
 * (RFC 8040 §3.1). Wrapping a test's fetch mock with withHostMeta answers that
 * discovery like a default twin does, and hands every other request to the
 * mock, so the mock's calls are the requests the page itself made.
 */

export function xrd(root: string): string {
  return (
    "<?xml version='1.0' encoding='UTF-8'?>\n" +
    "<XRD xmlns='http://docs.oasis-open.org/ns/xri/xrd-1.0'>\n" +
    `    <Link rel='restconf' href='${root}'/>\n` +
    "</XRD>\n"
  );
}

export function hostMetaResponse(root: string) {
  const body = xrd(root);
  return {
    ok: true,
    status: 200,
    text: () => Promise.resolve(body),
    json: () => Promise.reject(new Error("host-meta is XML")),
  };
}

type FetchLike = (url: string, init?: RequestInit) => unknown;

export function withHostMeta(handler: FetchLike, root = "/restconf"): FetchLike {
  return (url: string, init?: RequestInit) =>
    String(url).endsWith("/.well-known/host-meta")
      ? Promise.resolve(hostMetaResponse(root))
      : handler(url, init);
}
