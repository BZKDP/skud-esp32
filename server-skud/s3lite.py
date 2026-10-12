# -*- coding: utf-8 -*-
# Лёгкий S3-клиент на чистом stdlib (без boto3) для Timeweb S3.
# Умеет: put_object, delete_object, list_objects, presign_get. AWS Signature V4, path-style.
import os, hashlib, hmac, datetime, urllib.request, urllib.parse, xml.etree.ElementTree as ET

S3_HOST = os.getenv("S3_HOST", "s3.example-storage.com")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://" + S3_HOST)
BUCKET = os.getenv("S3_BUCKET", "")
REGION = os.getenv("S3_REGION", "ru-1")
SERVICE = "s3"
ACCESS = os.getenv("S3_ACCESS_KEY", "")
SECRET = os.getenv("S3_SECRET_KEY", "")

_UNSIGNED = "UNSIGNED-PAYLOAD"


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(datestamp: str) -> bytes:
    k = _hmac(("AWS4" + SECRET).encode("utf-8"), datestamp)
    k = _hmac(k, REGION)
    k = _hmac(k, SERVICE)
    k = _hmac(k, "aws4_request")
    return k


def _quote(s: str) -> str:
    # ключи S3: кодируем всё кроме безопасных и '/'
    return urllib.parse.quote(s, safe="/-_.~")


def _now():
    n = datetime.datetime.utcnow()
    return n.strftime("%Y%m%dT%H%M%SZ"), n.strftime("%Y%m%d")


def _request(method: str, key: str = "", data: bytes = b"", query_params=None,
             content_type: str = None, expect_xml: bool = False):
    amzdate, datestamp = _now()
    canonical_uri = "/" + BUCKET + (("/" + _quote(key)) if key else "")
    if key == "" and method == "GET":
        canonical_uri = "/" + BUCKET + "/"
    qp = query_params or {}
    canonical_query = "&".join(
        urllib.parse.quote(k, safe="") + "=" + urllib.parse.quote(str(v), safe="")
        for k, v in sorted(qp.items())
    )
    payload_hash = _sha256_hex(data)
    headers = {
        "host": S3_HOST,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amzdate,
    }
    if content_type:
        headers["content-type"] = content_type
    signed_headers = ";".join(sorted(headers.keys()))
    canonical_headers = "".join(k + ":" + headers[k] + "\n" for k in sorted(headers.keys()))
    canonical_request = "\n".join([method, canonical_uri, canonical_query,
                                   canonical_headers, signed_headers, payload_hash])
    scope = datestamp + "/" + REGION + "/" + SERVICE + "/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope, _sha256_hex(canonical_request.encode("utf-8"))])
    signature = hmac.new(_signing_key(datestamp), sts.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["Authorization"] = ("AWS4-HMAC-SHA256 Credential=" + ACCESS + "/" + scope +
                                ", SignedHeaders=" + signed_headers + ", Signature=" + signature)
    url = S3_ENDPOINT + canonical_uri + (("?" + canonical_query) if canonical_query else "")
    req = urllib.request.Request(url, data=(data if method in ("PUT", "POST") else None),
                                 method=method, headers=headers)
    resp = urllib.request.urlopen(req, timeout=60)
    body = resp.read()
    return resp.status, body


def put_object(key: str, data: bytes, content_type: str = "application/octet-stream"):
    return _request("PUT", key, data=data, content_type=content_type)[0]


def put_object_file(key: str, filepath: str, content_type: str = "video/mp4"):
    """Потоковая заливка файла (UNSIGNED-PAYLOAD) — не грузит файл целиком в память."""
    amzdate, datestamp = _now()
    canonical_uri = "/" + BUCKET + "/" + _quote(key)
    size = os.path.getsize(filepath)
    headers = {
        "host": S3_HOST,
        "x-amz-content-sha256": _UNSIGNED,
        "x-amz-date": amzdate,
        "content-type": content_type,
    }
    signed_headers = ";".join(sorted(headers.keys()))
    canonical_headers = "".join(k + ":" + headers[k] + "\n" for k in sorted(headers.keys()))
    canonical_request = "\n".join(["PUT", canonical_uri, "", canonical_headers, signed_headers, _UNSIGNED])
    scope = datestamp + "/" + REGION + "/" + SERVICE + "/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope, _sha256_hex(canonical_request.encode("utf-8"))])
    signature = hmac.new(_signing_key(datestamp), sts.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["Authorization"] = ("AWS4-HMAC-SHA256 Credential=" + ACCESS + "/" + scope +
                                ", SignedHeaders=" + signed_headers + ", Signature=" + signature)
    headers["Content-Length"] = str(size)
    f = open(filepath, "rb")
    try:
        req = urllib.request.Request(S3_ENDPOINT + canonical_uri, data=f, method="PUT", headers=headers)
        resp = urllib.request.urlopen(req, timeout=300)
        return resp.status
    finally:
        f.close()


def open_range(key: str, range_header: str = None):
    """Открывает объект (с опц. Range) и ВОЗВРАЩАЕТ urllib-ответ для стриминга. Вызывающий читает и закрывает."""
    url = presign_get(key, 600)
    headers = {}
    if range_header:
        headers["Range"] = range_header
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=30)


def delete_object(key: str):
    return _request("DELETE", key)[0]


def list_objects(prefix: str = "", max_keys: int = 1000):
    """Возвращает список ключей под prefix (с пагинацией)."""
    keys = []
    token = None
    while True:
        qp = {"list-type": "2", "prefix": prefix, "max-keys": str(max_keys)}
        if token:
            qp["continuation-token"] = token
        status, body = _request("GET", "", query_params=qp)
        root = ET.fromstring(body)
        ns = {"s3": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}
        def fa(el, tag):
            for child in el:
                if child.tag.endswith(tag):
                    return child
            return None
        for cont in root:
            if cont.tag.endswith("Contents"):
                k = fa(cont, "Key").text
                sz = fa(cont, "Size")
                lm = fa(cont, "LastModified")
                keys.append({"key": k, "size": int(sz.text) if sz is not None else 0,
                             "modified": lm.text if lm is not None else ""})
        trunc = None
        next_tok = None
        for child in root:
            if child.tag.endswith("IsTruncated"):
                trunc = child.text
            if child.tag.endswith("NextContinuationToken"):
                next_tok = child.text
        if trunc == "true" and next_tok:
            token = next_tok
        else:
            break
    return keys


def presign_get(key: str, expires: int = 3600) -> str:
    amzdate, datestamp = _now()
    canonical_uri = "/" + BUCKET + "/" + _quote(key)
    scope = datestamp + "/" + REGION + "/" + SERVICE + "/aws4_request"
    qp = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": ACCESS + "/" + scope,
        "X-Amz-Date": amzdate,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(
        urllib.parse.quote(k, safe="") + "=" + urllib.parse.quote(str(v), safe="")
        for k, v in sorted(qp.items())
    )
    canonical_headers = "host:" + S3_HOST + "\n"
    canonical_request = "\n".join(["GET", canonical_uri, canonical_query,
                                   canonical_headers, "host", _UNSIGNED])
    sts = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope, _sha256_hex(canonical_request.encode("utf-8"))])
    signature = hmac.new(_signing_key(datestamp), sts.encode("utf-8"), hashlib.sha256).hexdigest()
    return S3_ENDPOINT + canonical_uri + "?" + canonical_query + "&X-Amz-Signature=" + signature


if __name__ == "__main__":
    import sys, time
    print("self-test: put/list/presign/delete")
    tk = "rec/_selftest/hello.txt"
    payload = ("hello s3 " + time.strftime("%Y-%m-%d %H:%M:%S")).encode()
    print("PUT ->", put_object(tk, payload, "text/plain"))
    found = [o for o in list_objects("rec/_selftest/")]
    print("LIST ->", found)
    print("PRESIGN ->", presign_get(tk, 600))
    print("DELETE ->", delete_object(tk))
