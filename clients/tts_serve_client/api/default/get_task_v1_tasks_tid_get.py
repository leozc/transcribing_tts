from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.task_status import TaskStatus
from typing import cast



def _get_kwargs(
    tid: str,

) -> dict[str, Any]:
    

    

    

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/tasks/{tid}".format(tid=quote(str(tid), safe=""),),
    }


    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | TaskStatus | None:
    if response.status_code == 200:
        response_200 = TaskStatus.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | TaskStatus]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    tid: str,
    *,
    client: AuthenticatedClient | Client,

) -> Response[HTTPValidationError | TaskStatus]:
    """ Get Task

    Args:
        tid (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TaskStatus]
     """


    kwargs = _get_kwargs(
        tid=tid,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    tid: str,
    *,
    client: AuthenticatedClient | Client,

) -> HTTPValidationError | TaskStatus | None:
    """ Get Task

    Args:
        tid (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TaskStatus
     """


    return sync_detailed(
        tid=tid,
client=client,

    ).parsed

async def asyncio_detailed(
    tid: str,
    *,
    client: AuthenticatedClient | Client,

) -> Response[HTTPValidationError | TaskStatus]:
    """ Get Task

    Args:
        tid (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TaskStatus]
     """


    kwargs = _get_kwargs(
        tid=tid,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    tid: str,
    *,
    client: AuthenticatedClient | Client,

) -> HTTPValidationError | TaskStatus | None:
    """ Get Task

    Args:
        tid (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TaskStatus
     """


    return (await asyncio_detailed(
        tid=tid,
client=client,

    )).parsed
