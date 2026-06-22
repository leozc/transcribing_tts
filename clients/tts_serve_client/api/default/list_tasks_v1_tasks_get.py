from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.task_list import TaskList
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    *,
    client_id: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(x_client_key, Unset):
        headers["x-client-key"] = x_client_key



    

    params: dict[str, Any] = {}

    json_client_id: None | str | Unset
    if isinstance(client_id, Unset):
        json_client_id = UNSET
    else:
        json_client_id = client_id
    params["client_id"] = json_client_id


    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}


    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/tasks",
        "params": params,
    }


    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | TaskList | None:
    if response.status_code == 200:
        response_200 = TaskList.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | TaskList]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    client_id: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | TaskList]:
    """ List Tasks

    Args:
        client_id (None | str | Unset):
        x_client_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TaskList]
     """


    kwargs = _get_kwargs(
        client_id=client_id,
x_client_key=x_client_key,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    *,
    client: AuthenticatedClient | Client,
    client_id: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> HTTPValidationError | TaskList | None:
    """ List Tasks

    Args:
        client_id (None | str | Unset):
        x_client_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TaskList
     """


    return sync_detailed(
        client=client,
client_id=client_id,
x_client_key=x_client_key,

    ).parsed

async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    client_id: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | TaskList]:
    """ List Tasks

    Args:
        client_id (None | str | Unset):
        x_client_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TaskList]
     """


    kwargs = _get_kwargs(
        client_id=client_id,
x_client_key=x_client_key,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    client_id: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> HTTPValidationError | TaskList | None:
    """ List Tasks

    Args:
        client_id (None | str | Unset):
        x_client_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TaskList
     """


    return (await asyncio_detailed(
        client=client,
client_id=client_id,
x_client_key=x_client_key,

    )).parsed
