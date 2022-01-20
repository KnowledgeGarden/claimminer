"""
Copyright Society Library and Conversence 2022-2023
"""
from datetime import timedelta

from quart import render_template, redirect, request
from quart_auth import (
    AuthUser, login_user, logout_user, Unauthorized, login_required
)
from quart_jwt_extended import create_access_token
from passlib.hash import scram

from .. import Session, select, as_bool
from . import get_base_template_vars
from ..models import User, permission, CollectionPermissions, Collection
from ..app import app, logger, current_user
from ..auth import requires_permission


@app.errorhandler(Unauthorized)
async def redirect_to_login(*_):
    return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
async def login():
    error = ""
    if request.method == "POST":
        form = await request.form
        username = form.get("username")
        password = form.get("password")
        if not username:
            error += "Missing username "
        if not password:
            error += "Missing password "
        if not error:
            async with Session() as session:
                base_vars = await get_base_template_vars(current_user, None, session)
                try:
                    r = await session.execute(
                        select(User.id, User.passwd, User.confirmed).filter_by(handle=username))
                    r = r.first()
                    (user_id, passwd, confirmed) = r
                    if not confirmed:
                        error = "Account not confirmed"
                    elif scram.verify(password, passwd):
                        login_user(AuthUser(user_id))
                        logger.debug("Logged in!")
                        return redirect("/")
                    else:
                        error = "Invalid password"
                except TypeError:
                    error = f"Invalid username {username}"
    else:
        base_vars = await get_base_template_vars(current_user)
    return await render_template("login.html", error=error, **base_vars)

@app.route("/register", methods=["GET", "POST"])
async def register():
    error = ""
    if request.method == "POST":
        form = await request.form
        username = form.get("username")
        password = form.get("password")
        email = form.get("email")
        if not username:
            error += "Missing username "
        if not password:
            error += "Missing password "
        if not email:
            error += "Missing email "
        if not error:
            try:
                async with Session() as session:
                    base_vars = await get_base_template_vars(current_user, None, session)
                    existing = await session.execute(
                        select(User.id, User.passwd, User.confirmed).filter_by(handle=username))
                    existing = existing.first()
                    if existing:
                        error = f"Username {username} already exists"
                    else:
                        user = User(handle=username, passwd=scram.hash(password), email=email)
                        session.add(user)
                        await session.commit()
                        return f"User {username} created. Please wait for approval by administrator."
            except Exception as e:
                error = f"Error creating user {username}: {e}"
    else:
        base_vars = await get_base_template_vars(current_user)
    return await render_template("register.html", error=error, **base_vars)


@app.route("/logout")
async def logout():
    logout_user()
    return redirect("/login")

@app.route("/admin", methods=["GET", "POST"])
@app.route("/c/<collection>/admin", methods=["GET", "POST"])
@requires_permission("admin")
async def admin(collection=None):
    error = ""
    users = []
    try:
        async with Session() as session:
            base_vars = await get_base_template_vars(current_user, collection, session)
            collection = base_vars['collection']
            users = await session.execute(select(User).order_by(User.email))
            users = [user for (user,) in users]
            if collection:
                await session.refresh(collection, ['permissions'])
                permissions_per_user = {p.user_id: p for p in collection.permissions}
            else:
                permissions_per_user = {}
            if request.method == "POST":
                form = await request.form
                for user in users:
                    confirmed = as_bool(form.get(f"{user.id}_confirmed"))
                    if confirmed != user.confirmed:
                        user.confirmed = confirmed
                        session.add(user)
                    new_permissions = {
                        p for p in permission.enums
                        if as_bool(form.get(f"{user.id}_{p}"))
                    }
                    if collection:
                        new_permissions = {p for p in new_permissions if not user.can(p)}
                        r = permissions_per_user.get(user.id)
                        if new_permissions or r:
                            r = r or CollectionPermissions(user_id=user.id, collection_id=collection.id)
                            if set(r.permissions or ()) != new_permissions:
                                r.permissions = list(new_permissions)
                            session.add(r)
                    else:
                        if set(user.permissions or ()) != new_permissions:
                            user.permissions = list(new_permissions)
                            session.add(user)
                await session.commit()
    except Exception as e:
        logger.exception(e)
        error = str(e)
    return await render_template("admin.html", users=users, error=error, permissions_per_user=permissions_per_user, **base_vars)


@app.route("/token", methods=["POST", "GET"])
@login_required
async def get_token():
    delta = timedelta(hours=6)
    if request.method == 'GET':
        delta = timedelta(seconds=request.args.get('seconds', delta.seconds))
    access_token = create_access_token(current_user.auth_id, expires_delta=delta)
    return dict(access_token=access_token)
