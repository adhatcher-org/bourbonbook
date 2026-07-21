import asyncio

import httpx

from bourbonbook.config import Settings
from bourbonbook.models import CatalogPrice
from bourbonbook.qdrant_prices import QdrantPriceIndex, sparse_text_vector


def settings(tmp_path):
    return Settings(
        tmp_path,
        "sqlite://",
        "test",
        False,
        "http://ollama",
        "test",
        1,
        1,
        qdrant_url="http://qdrant",
    )


class Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class Client:
    def __init__(self, **kwargs):
        self.calls = []

    async def aclose(self):
        pass

    async def get(self, url):
        self.calls.append(("get", url))
        return Response({"result": {"exists": False}})

    async def put(self, url, **kwargs):
        self.calls.append(("put", url, kwargs))
        return Response({"result": {}})

    async def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return Response(
            {"result": {"points": [{"score": 0.95, "payload": {"catalog_price_id": 7}}]}}
        )


def test_qdrant_index_creates_writes_and_finds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("bourbonbook.qdrant_prices.httpx.AsyncClient", Client)
    index = QdrantPriceIndex(settings(tmp_path))
    price = CatalogPrice(id=7, product_key="example bourbon", size_key="750ml", msrp=49.99, url="")

    assert asyncio.run(index.ensure_collection()) is True
    assert asyncio.run(index.upsert(price)) is True
    match = asyncio.run(index.find("example bourbon", "750ml"))
    asyncio.run(index.close())

    assert match and match.catalog_price_id == 7
    assert match.score == 0.95
    assert any(call[0] == "put" for call in index.client.calls)


def test_qdrant_index_handles_disabled_and_http_errors(tmp_path, monkeypatch) -> None:
    assert (
        asyncio.run(
            QdrantPriceIndex(
                Settings(tmp_path, "sqlite://", "test", False, "http://ollama", "test", 1, 1)
            ).ensure_collection()
        )
        is False
    )

    class FailingClient(Client):
        async def get(self, url):
            raise httpx.ConnectError("nope", request=httpx.Request("GET", url))

    monkeypatch.setattr("bourbonbook.qdrant_prices.httpx.AsyncClient", FailingClient)
    assert asyncio.run(QdrantPriceIndex(settings(tmp_path)).ensure_collection()) is False


def test_sparse_text_vector_is_stable_and_normalized() -> None:
    first = sparse_text_vector("New Riff 8 Year Bourbon")
    second = sparse_text_vector("new-riff 8 year bourbon")

    assert first == second
    assert first["indices"] == sorted(first["indices"])
    assert round(sum(value * value for value in first["values"]), 5) == 1.0


def test_sparse_text_vector_ignores_empty_identity() -> None:
    assert sparse_text_vector("") == {"indices": [], "values": []}
