################################################################################
#
#  New Zealand Geographic Board gazetteer application,
#  Crown copyright (c) 2020, Land Information New Zealand on behalf of
#  the New Zealand Government.
#
#  This file is released under the MIT licence. See the LICENCE file found
#  in the top-level directory of this distribution for more information.
#
################################################################################

import re
import os
import sys
import getpass
from typing import Optional, Dict

import sqlalchemy
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.sql import expression

from sqlalchemy import event
from sqlalchemy.pool import Pool
from sqlalchemy.sql import text

_instance = None

func = expression.func

from . import Config


class Database(object):
    HOST: Optional[str] = None
    PORT: Optional[str] = None
    DATABASE: Optional[str] = None
    SCHEMA: Optional[str] = None
    USER: Optional[str] = None
    PASSWORD: Optional[str] = None

    def __init__(self):
        connection_string = "/" + Database.DATABASE + "?host=" + Database.HOST
        if Database.PORT:
            connection_string += "&port=" + Database.PORT

        if Database.USER:
            user_string = Database.USER + ":" + (Database.PASSWORD or "")
            connection_string = user_string + "@" + connection_string
        connection_string = "postgresql+psycopg2://" + connection_string

        self._engine = sqlalchemy.create_engine(connection_string)
        # event.listen(self._engine, 'connect', set_search_path )
        event.listen(Pool, "connect", Database.set_search_path)
        self._session = None

    def engine(self):
        return self._engine

    def session(self):
        if not self._session:
            Session = scoped_session(sessionmaker(bind=self._engine))
            self._session = Session()
            sql = "set search_path=" + Database.SCHEMA + ", public"
            self._session.execute(sql)
        return self._session

    @staticmethod
    def set_search_path(db_conn, conn_proxy):
        sql = "set search_path=" + Database.SCHEMA + ", public"
        db_conn.cursor().execute(sql)

    @classmethod
    def update_connection_details(cls):
        # default database connection parameters
        # prefer QSettings, then environment, finally hardcoded defaults
        # note that if the QSettings "database" key exists, then we get ALL
        # the database properties from QSettings -- we don't want to fallback to
        # env variables or defaults if a particular configuration key isn't
        # applicable to the stored connection and is set to "" or None
        cls.HOST = (
            Config.get("Database/host", None)
            if Config.contains("Database")
            else (os.environ.get("PGHOST") or "prdassgzdb01")
        )
        cls.PORT = (
            Config.get("Database/port", None)
            if Config.contains("Database")
            else (os.environ.get("PGPORT") or "5432")
        )
        cls.DATABASE = (
            Config.get("Database/database", None)
            if Config.contains("Database")
            else (os.environ.get("PGDATABASE") or "gazetteer")
        )
        cls.SCHEMA = (
            Config.get("Database/schema", None)
            if Config.contains("Database")
            else (os.environ.get("PGSCHEMA") or "gazetteer")
        )
        cls.USER = (
            Config.get("Database/user", None)
            if Config.contains("Database")
            else (os.environ.get("PGUSER") or getpass.getuser())
        )
        cls.PASSWORD = (
            Config.get("Database/password", None)
            if Config.contains("Database")
            else (os.environ.get("PGPASSWORD") or None)
        )

    @classmethod
    def get_configuration(cls) -> Dict[str, Optional[str]]:
        return dict(
            host=cls.DATABASE or None,
            port=cls.PORT or None,
            database=cls.DATABASE or None,
            schema=cls.SCHEMA or None,
            user=cls.USER or None,
            password=cls.PASSWORD or None,
        )

    @classmethod
    def set_connection(
        cls,
        host: Optional[str] = None,
        port: Optional[str] = None,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        changed = False
        if host is not None and host != Database.HOST:
            Database.HOST = host
            changed = True
        if port is not None and port != Database.PORT:
            Database.PORT = port
            changed = True
        if database is not None and database != Database.DATABASE:
            Database.DATABASE = database
            changed = True
        if schema is not None and schema != schema:
            Database.SCHEMA = schema
            changed = True
        if user is not None and user != Database.USER:
            Database.USER = user
            changed = True
        if password is not None and password != Database.PASSWORD:
            Database.PASSWORD = password
            changed = True
        if changed and _instance:
            raise RuntimeError(
                "Cannot set connection to database after it has been instantiated"
            )

    @classmethod
    def get_connection(cls) -> Dict[str, Optional[str]]:
        return {
            "host": Database.HOST,
            "port": Database.PORT,
            "database": Database.DATABASE,
            "schema": Database.SCHEMA,
            "user": Database.USER,
            "password": Database.PASSWORD,
        }


Database.update_connection_details()


def instance():
    global _instance
    admins = None
    if not _instance:
        try:
            _instance = Database()
            if not userIsValid():
                _instance = None
                admins = gazetteerAdmins()
        except:
            msg = str(sys.exc_info()[1])
            raise RuntimeError(
                "Current user "
                + str(Database.USER)
                + " is not authorized to access the gazetteer database.\n"
                + msg
            )

    if not _instance:
        raise RuntimeError(
            "Current user "
            + str(Database.USER)
            + " is not authorized to access the gazetteer database\n"
            + "Contact a gazetteer admin:\n    "
            + "\n    ".join(admins)
        )

    return _instance


def engine():
    return instance().engine()


def session():
    return instance().session()


def commit():
    try:
        session().commit()
    except:
        session().rollback()
        raise


def rollback():
    session().rollback()


def add(object_):
    session().add(object_)


def delete(object_):
    session().delete(object_)


def scalar(sql, **kwargs):
    if type(sql) in (str, str):
        sql = text(sql)
    try:
        return session().scalar(sql, kwargs)
    except:
        rollback()
        raise


def query(*args, **kwargs):
    return session().query(*args, **kwargs)


def querysql(sql, **kwargs):
    if type(sql) in (str, str):
        sql = text(sql)
    try:
        return session().execute(sql, kwargs)
    except:
        rollback()
        raise


def execute(sql, **kwargs):
    if type(sql) in (str, str):
        sql = text(sql)
    try:
        session().execute(sql, kwargs)
        commit()
    except:
        rollback()
        raise


def build_tsquery(text):
    text = scalar("select gazetteer.gaz_plainText2(:text)", text=text)
    return " & ".join([re.sub(r"\*$", ":*", x) for x in text.split()])


def user():
    return scalar("select current_user")


def users():
    return [
        dict(userid=r[0], isdba=r[1])
        for r in querysql("select userid, isdba from gazetteer.gazetteer_users")
    ]


def userIsValid():
    return scalar("select gazetteer.gaz_IsGazetteerUser()")


def userIsDba():
    return scalar("select gazetteer.gaz_IsGazetteerDba()")


def gazetteerAdmins():
    admins = []
    for r in querysql("select userid from gazetteer_users where isdba"):
        admins.append(str(r[0]))
    return admins
