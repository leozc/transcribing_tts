from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.create_task_request import CreateTaskRequest
from ...models.http_validation_error import HTTPValidationError
from ...models.task_ref import TaskRef
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    *,
    body: CreateTaskRequest,
    x_client_key: None | str | Unset = UNSET,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(x_client_key, Unset):
        headers["x-client-key"] = x_client_key



    

    

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/v1/tasks",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | TaskRef | None:
    if response.status_code == 200:
        response_200 = TaskRef.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | TaskRef]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateTaskRequest,
    x_client_key: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | TaskRef]:
    """ Create Task

    Args:
        x_client_key (None | str | Unset):
        body (CreateTaskRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TaskRef]
     """


    kwargs = _get_kwargs(
        body=body,
x_client_key=x_client_key,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    *,
    client: AuthenticatedClient | Client,
    body: CreateTaskRequest,
    x_client_key: None | str | Unset = UNSET,

) -> HTTPValidationError | TaskRef | None:
    """ Create Task

    Args:
        x_client_key (None | str | Unset):
        body (CreateTaskRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TaskRef
     """


    return sync_detailed(
        client=client,
body=body,
x_client_key=x_client_key,

    ).parsed

async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateTaskRequest,
    x_client_key: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | TaskRef]:
    """ Create Task

    Args:
        x_client_key (None | str | Unset):
        body (CreateTaskRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TaskRef]
     """


    kwargs = _get_kwargs(
        body=body,
x_client_key=x_client_key,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: CreateTaskRequest,
    x_client_key: None | str | Unset = UNSET,

) -> HTTPValidationError | TaskRef | None:
    """ Create Task

    Args:
        x_client_key (None | str | Unset):
        body (CreateTaskRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TaskRef
     """


    return (await asyncio_detailed(
        client=client,
body=body,
x_client_key=x_client_key,

    )).parsed
