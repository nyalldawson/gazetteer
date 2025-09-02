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

func = expression.func

from . import Config


class Database(object):
    HOST: Optional[str] = None
    PORT: Optional[str] = None
    DATABASE: Optional[str] = None
    SCHEMA: Optional[str] = None
    USER: Optional[str] = None
    PASSWORD: Optional[str] = None

    _INSTANCE: Optional["Database"] = None

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
        old_host = cls.HOST
        old_port = cls.PORT
        old_database = cls.DATABASE
        old_schema = cls.SCHEMA
        old_user = cls.USER
        old_password = cls.PASSWORD

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

        changed = (
            cls.HOST != old_host
            or cls.PORT != old_port
            or cls.DATABASE != old_database
            or cls.SCHEMA != old_schema
            or cls.USER != old_user
            or cls.PASSWORD != old_password
        )
        if changed and cls._INSTANCE:
            raise RuntimeError(
                "Cannot change database connection parameters after it has been instantiated"
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
    def get_connection(cls) -> Dict[str, Optional[str]]:
        return {
            "host": Database.HOST,
            "port": Database.PORT,
            "database": Database.DATABASE,
            "schema": Database.SCHEMA,
            "user": Database.USER,
            "password": Database.PASSWORD,
        }

    @classmethod
    def instance(cls) -> "Database":
        admins = None
        if not cls._INSTANCE:
            try:
                cls._INSTANCE = Database()
                if not userIsValid():
                    cls._INSTANCE = None
                    admins = gazetteerAdmins()
            except:
                msg = str(sys.exc_info()[1])
                raise RuntimeError(
                    "Current user "
                    + str(cls.USER)
                    + " is not authorized to access the gazetteer database.\n"
                    + msg
                )

        if not cls._INSTANCE:
            raise RuntimeError(
                "Current user "
                + str(cls.USER)
                + " is not authorized to access the gazetteer database\n"
                + "Contact a gazetteer admin:\n    "
                + "\n    ".join(admins)
            )

        return cls._INSTANCE


Database.update_connection_details()


def engine():
    return Database.instance().engine()


def session():
    return Database.instance().session()


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
