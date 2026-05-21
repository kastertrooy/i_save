from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, status
from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Integer, String, Text, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from admin_panel.middleware.auth_middleware import get_current_user
from shared.database.connection import get_db
from shared.database.models import Base, User
from shared.logger import get_logger


logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/database', tags=['database'])
TABLE_LABELS = {
    'brute_force_log': 'Защита от входа',
    'content_queue': 'Очередь скачивания',
    'delivery_logs': 'Доставки файлов',
    'direct_message_logs': 'Сообщения Instagram Direct',
    'instagram_accounts': 'Instagram аккаунты',
    'media_cache': 'Кэш медиа',
    'notification_log': 'Уведомления',
    'proxy_list': 'Прокси',
    'service_instances': 'Сервисы',
    'staff_accounts': 'Администраторы и сотрудники',
    'staff_action_logs': 'Действия админов',
    'subscription_logs': 'Подписки',
    'system_settings': 'Настройки системы',
    'telegram_storage_groups': 'Группы хранения Telegram',
    'users': 'Пользователи',
    'worker_schedules': 'Расписание воркеров',
}


def _require_admin(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
) -> tuple[int, str]:
    staff_id, role = get_current_user(authorization, refresh_token)
    if role != 'admin':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Admin access required')
    return (staff_id, role)


def _tables() -> dict[str, Any]:
    return {table.name: table for table in Base.metadata.sorted_tables}


def _column_type(column: Any) -> str:
    column_type = column.type
    if isinstance(column_type, Boolean):
        return 'boolean'
    if isinstance(column_type, (Integer, BigInteger)):
        return 'integer'
    if isinstance(column_type, DateTime):
        return 'datetime'
    if isinstance(column_type, JSON):
        return 'json'
    if isinstance(column_type, (String, Text)):
        return 'string'
    return column_type.__class__.__name__.lower()


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _extract_direct_username(raw_data: Any, instagram_id: Any) -> str | None:
    if not isinstance(raw_data, dict):
        return None

    username = raw_data.get('username')
    if isinstance(username, str) and username:
        return username

    user_data = raw_data.get('user')
    if isinstance(user_data, dict):
        username = user_data.get('username')
        if isinstance(username, str) and username:
            return username

    users = raw_data.get('users')
    if isinstance(users, list):
        for user in users:
            if not isinstance(user, dict):
                continue
            user_id = user.get('pk') or user.get('id') or user.get('user_id')
            username = user.get('username')
            if username and (not instagram_id or str(user_id) == str(instagram_id)):
                return username

    return None


def _coerce_value(column: Any, value: Any) -> Any:
    if value == '':
        return None

    column_type = column.type
    if isinstance(column_type, Boolean):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    if isinstance(column_type, (Integer, BigInteger)):
        return int(value) if value is not None else None
    if isinstance(column_type, DateTime):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    if isinstance(column_type, JSON):
        return value
    return str(value) if value is not None else None


@router.get('/tables')
async def list_tables(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
) -> dict:
    _require_admin(authorization, refresh_token)
    return {
        'tables': [
            {
                'name': table_name,
                'display_name': TABLE_LABELS.get(table_name, table_name),
                'columns': [
                    {
                        'name': column.name,
                        'type': _column_type(column),
                        'nullable': column.nullable,
                        'primary_key': column.primary_key,
                    }
                    for column in table.columns
                ],
            }
            for table_name, table in _tables().items()
        ]
    }


@router.get('/tables/{table_name}/rows')
async def list_rows(
    table_name: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_admin(authorization, refresh_token)
    table = _tables().get(table_name)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Table not found')

    total_result = await db.execute(select(func.count()).select_from(table))
    total = total_result.scalar_one() or 0

    order_columns = list(table.primary_key.columns) or list(table.columns)
    result = await db.execute(select(table).order_by(*order_columns).offset(skip).limit(limit))
    rows = result.mappings().all()
    serialized_rows = [{key: _serialize_value(value) for key, value in row.items()} for row in rows]
    if table_name == 'direct_message_logs':
        instagram_ids = {row.get('instagram_id') for row in serialized_rows if row.get('instagram_id')}
        usernames_by_instagram_id = {}
        if instagram_ids:
            users_result = await db.execute(
                select(User.instagram_id, User.instagram_username).where(User.instagram_id.in_(instagram_ids))
            )
            usernames_by_instagram_id = {
                instagram_id: instagram_username
                for instagram_id, instagram_username in users_result.all()
                if instagram_username
            }
        for row in serialized_rows:
            username = (
                _extract_direct_username(row.get('raw_data'), row.get('instagram_id'))
                or usernames_by_instagram_id.get(row.get('instagram_id'))
            )
            if username:
                row['_instagram_username'] = username

    return {
        'total': total,
        'skip': skip,
        'limit': limit,
        'columns': [column.name for column in table.columns],
        'rows': serialized_rows,
    }


@router.post('/tables/{table_name}/rows')
async def create_row(
    table_name: str,
    payload: dict[str, Any],
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    staff_id, _ = _require_admin(authorization, refresh_token)
    table = _tables().get(table_name)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Table not found')

    values = {}
    for column in table.columns:
        if column.name not in payload:
            continue
        value = payload[column.name]
        if column.primary_key and value in (None, ''):
            continue
        if value in (None, '') and column.nullable:
            values[column.name] = None
            continue
        try:
            values[column.name] = _coerce_value(column, value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Invalid value for {column.name}: {exc}',
            )

    missing = [
        column.name
        for column in table.columns
        if (
            not column.nullable
            and not column.primary_key
            and column.default is None
            and column.server_default is None
            and column.name not in values
        )
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Missing required columns: {", ".join(missing)}',
        )

    result = await db.execute(insert(table).values(**values).returning(*table.columns))
    await db.commit()
    row = result.mappings().one()
    logger.info('Admin %s inserted row into %s', staff_id, table_name)
    return {'row': {key: _serialize_value(value) for key, value in row.items()}}


@router.patch('/tables/{table_name}/rows/{pk_value}')
async def update_row(
    table_name: str,
    pk_value: str,
    payload: dict[str, Any],
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    staff_id, _ = _require_admin(authorization, refresh_token)
    table = _tables().get(table_name)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Table not found')

    primary_keys = list(table.primary_key.columns)
    if len(primary_keys) != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Editing is supported only for tables with one primary key',
        )

    pk_column = primary_keys[0]
    coerced_pk = _coerce_value(pk_column, pk_value)
    values = {}
    for column in table.columns:
        if column.primary_key or column.name not in payload:
            continue
        value = payload[column.name]
        if value in (None, '') and column.nullable:
            values[column.name] = None
            continue
        try:
            values[column.name] = _coerce_value(column, value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Invalid value for {column.name}: {exc}',
            )

    if not values:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No editable values provided')

    result = await db.execute(
        update(table)
        .where(pk_column == coerced_pk)
        .values(**values)
        .returning(*table.columns)
    )
    row = result.mappings().one_or_none()
    if row is None:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Row not found')

    await db.commit()
    logger.info('Admin %s updated row %s in %s', staff_id, pk_value, table_name)
    return {'row': {key: _serialize_value(value) for key, value in row.items()}}


@router.delete('/tables/{table_name}/rows/{pk_value}')
async def delete_row(
    table_name: str,
    pk_value: str,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    staff_id, _ = _require_admin(authorization, refresh_token)
    table = _tables().get(table_name)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Table not found')

    primary_keys = list(table.primary_key.columns)
    if len(primary_keys) != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Deleting is supported only for tables with one primary key',
        )

    pk_column = primary_keys[0]
    coerced_pk = _coerce_value(pk_column, pk_value)
    result = await db.execute(
        delete(table)
        .where(pk_column == coerced_pk)
        .returning(pk_column)
    )
    deleted_pk = result.scalar_one_or_none()
    if deleted_pk is None:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Row not found')

    await db.commit()
    logger.info('Admin %s deleted row %s from %s', staff_id, pk_value, table_name)
    return {'deleted': True, 'primary_key': _serialize_value(deleted_pk)}
