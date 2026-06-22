from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.task_ref import TaskRef
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    tid: str,
    *,
    token: None | str | Unset = UNSET,
    x_task_token: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(x_task_token, Unset):
        headers["x-task-token"] = x_task_token

    if not isinstance(x_client_key, Unset):
        headers["x-client-key"] = x_client_key



    

    params: dict[str, Any] = {}

    json_token: None | str | Unset
    if isinstance(token, Unset):
        json_token = UNSET
    else:
        json_token = token
    params["token"] = json_token


    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}


    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/v1/tasks/{tid}/retry".format(tid=quote(str(tid), safe=""),),
        "params": params,
    }


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
    tid: str,
    *,
    client: AuthenticatedClient | Client,
    token: None | str | Unset = UNSET,
    x_task_token: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | TaskRef]:
    """ Retry Task

    Args:
        tid (str):
        token (None | str | Unset):
        x_task_token (None | str | Unset):
        x_client_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TaskRef]
     """


    kwargs = _get_kwargs(
        tid=tid,
token=token,
x_task_token=x_task_token,
x_client_key=x_client_key,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    tid: str,
    *,
    client: AuthenticatedClient | Client,
    token: None | str | Unset = UNSET,
    x_task_token: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> HTTPValidationError | TaskRef | None:
    """ Retry Task

    Args:
        tid (str):
        token (None | str | Unset):
        x_task_token (None | str | Unset):
        x_client_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TaskRef
     """


    return sync_detailed(
        tid=tid,
client=client,
token=token,
x_task_token=x_task_token,
x_client_key=x_client_key,

    ).parsed

async def asyncio_detailed(
    tid: str,
    *,
    client: AuthenticatedClient | Client,
    token: None | str | Unset = UNSET,
    x_task_token: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | TaskRef]:
    """ Retry Task

    Args:
        tid (str):
        token (None | str | Unset):
        x_task_token (None | str | Unset):
        x_client_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TaskRef]
     """


    kwargs = _get_kwargs(
        tid=tid,
token=token,
x_task_token=x_task_token,
x_client_key=x_client_key,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    tid: str,
    *,
    client: AuthenticatedClient | Client,
    token: None | str | Unset = UNSET,
    x_task_token: None | str | Unset = UNSET,
    x_client_key: None | str | Unset = UNSET,

) -> HTTPValidationError | TaskRef | None:
    """ Retry Task

    Args:
        tid (str):
        token (None | str | Unset):
        x_task_token (None | str | Unset):
        x_client_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TaskRef
     """


    return (await asyncio_detailed(
        tid=tid,
client=client,
token=token,
x_task_token=x_task_token,
x_client_key=x_client_key,

    )).parsed
