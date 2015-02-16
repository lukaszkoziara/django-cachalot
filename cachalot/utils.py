# coding: utf-8

from __future__ import unicode_literals
from hashlib import sha1
from time import time

import django
from django.db import connections
from django.db.models.sql.where import ExtraWhere
if django.VERSION[:2] >= (1, 7):
    from django.utils.module_loading import import_string
else:
    from django.utils.module_loading import import_by_path as import_string

from .settings import cachalot_settings


# The only cache backend with a key length limit is memcached (limited to
# 255 characters). However, we hash keys on other backends as well to avoid
# unnecessary huge communication between processes.
# We set the limit to something smaller than 255 because a prefix might be
# added by Django.
MAX_CACHE_KEY_LENGTH = 200


def get_query_cache_key(compiler):
    """
    Generates a cache key from a SQLCompiler.

    This cache key is specific to the SQL query and its context
    (which database is used).  The same query in the same context
    (= the same database) must generate the same cache key.

    :arg compiler: A SQLCompiler that will generate the SQL query
    :type compiler: django.db.models.sql.compiler.SQLCompiler
    :return: A cache key
    :rtype: str
    """
    sql, params = compiler.as_sql()
    cache_key = ('%s:%s:%s' % (compiler.using, sql, params)).encode('utf-8')
    # We always hash queries since they are nearly always longer than
    # ``MAX_CACHE_KEY_LENGTH``.
    return sha1(cache_key).hexdigest()


def get_table_cache_key(db_alias, table):
    """
    Generates a cache key from a SQL table.

    :arg db_alias: Alias of the used database
    :type db_alias: str or unicode
    :arg table: Name of the SQL table
    :type table: str or unicode
    :return: A cache key
    :rtype: str
    """
    cache_key = ('%s:%s' % (db_alias, table)).encode('utf-8')
    # We check if we have to hash the key since it should nearly never be
    # necessary.
    if len(cache_key) > MAX_CACHE_KEY_LENGTH:
        return sha1(cache_key).hexdigest()
    return cache_key


def _get_query_cache_key(compiler):
    return import_string(cachalot_settings.CACHALOT_QUERY_KEYGEN)(compiler)


def _get_table_cache_key(db_alias, table):
    return import_string(cachalot_settings.CACHALOT_TABLE_KEYGEN)(db_alias, table)


def _get_tables_from_sql(connection, lowercased_sql):
    return [t for t in connection.introspection.django_table_names()
            if t in lowercased_sql]


def _get_table_cache_keys(compiler):
    """
    Returns a ``list`` of cache keys for all the SQL tables used
    by ``compiler``.

    :arg compiler: A SQLCompiler that will generate the SQL query
    :type compiler: django.db.models.sql.compiler.SQLCompiler
    :return: Cache keys for the SQL tables used
    :rtype: list
    """

    query = compiler.query
    using = compiler.using

    tables = set(query.table_map)
    tables.add(query.model._meta.db_table)
    if query.extra_select or any([isinstance(c, ExtraWhere)
                                  for c in query.where.children]):
        sql = compiler.as_sql()[0].lower()
        connection = connections[using]
        additional_tables = _get_tables_from_sql(connection, sql)
        tables.update(additional_tables)
    return [_get_table_cache_key(using, t) for t in tables]


def _invalidate_table_cache_keys(cache, table_cache_keys):
    if hasattr(cache, 'to_be_invalidated'):
        cache.to_be_invalidated.update(table_cache_keys)
    now = time()
    d = {}
    for k in table_cache_keys:
        d[k] = now
    cache.set_many(d, None)


def _invalidate_tables(cache, compiler):
    table_cache_keys = _get_table_cache_keys(compiler)
    _invalidate_table_cache_keys(cache, table_cache_keys)
