"""团队服务（CRUD/树/成员管理）。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..common import next_snowflake_id
from ..models import Team, TeamMember, User
from ..schemas import CreateTeamDto, QueryTeamDto, UpdateTeamDto


def _to_dict(team: Team) -> dict:
    return {
        "id": team.id,
        "teamName": team.team_name,
        "teamCode": team.team_code,
        "description": team.description,
        "leaderId": team.leader_id,
        "parentId": team.parent_id,
        "sort": team.sort,
        "status": team.status,
        "createdAt": team.created_at,
        "updatedAt": team.updated_at,
    }


class TeamService:
    async def create(self, session: AsyncSession, dto: CreateTeamDto) -> dict:
        team = Team(
            id=next_snowflake_id(),
            team_name=dto.teamName,
            team_code=dto.teamCode,
            description=dto.description,
            leader_id=dto.leaderId,
            parent_id=dto.parentId or "0",
            sort=dto.sort or 0,
            status=dto.status or 1,
        )
        session.add(team)
        await session.flush()
        return _to_dict(team)

    async def update(self, session: AsyncSession, team_id: str, dto: UpdateTeamDto) -> dict:
        team = await self._find_by_id_or_throw(session, team_id)
        if dto.teamName is not None:
            team.team_name = dto.teamName
        if dto.teamCode is not None:
            team.team_code = dto.teamCode
        if dto.description is not None:
            team.description = dto.description
        if dto.leaderId is not None:
            team.leader_id = dto.leaderId
        if dto.parentId is not None:
            if dto.parentId == team_id:
                raise HTTPException(400, "父团队不能是自己")
            team.parent_id = dto.parentId
        if dto.sort is not None:
            team.sort = dto.sort
        if dto.status is not None:
            team.status = dto.status
        await session.flush()
        return _to_dict(team)

    async def delete(self, session: AsyncSession, team_id: str) -> None:
        team = await self._find_by_id_or_throw(session, team_id)
        child_stmt = select(func.count(Team.id)).where(
            Team.parent_id == team_id, Team.deleted.is_(False)
        )
        child_count = (await session.execute(child_stmt)).scalar() or 0
        if child_count > 0:
            raise HTTPException(400, "存在子团队，无法删除")
        team.deleted = True
        await session.flush()
        await session.execute(sa_delete(TeamMember).where(TeamMember.team_id == team_id))

    async def get_detail(self, session: AsyncSession, team_id: str) -> dict:
        team = await self._find_by_id_or_throw(session, team_id)
        count_stmt = select(func.count(TeamMember.id)).where(
            TeamMember.team_id == team_id
        )
        member_count = (await session.execute(count_stmt)).scalar() or 0
        return {**_to_dict(team), "memberCount": member_count}

    async def page(self, session: AsyncSession, query: QueryTeamDto) -> dict:
        page = query.page or 1
        page_size = query.pageSize or 20
        stmt = select(Team).where(Team.deleted.is_(False))
        total_stmt = select(func.count(Team.id)).where(Team.deleted.is_(False))
        if query.keyword and query.keyword.strip():
            kw = f"%{query.keyword.strip()}%"
            cond = or_(Team.team_name.ilike(kw), Team.team_code.ilike(kw))
            stmt = stmt.where(cond)
            total_stmt = total_stmt.where(cond)
        if query.status is not None:
            stmt = stmt.where(Team.status == query.status)
            total_stmt = total_stmt.where(Team.status == query.status)

        stmt = (
            stmt.order_by(Team.sort.asc(), Team.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [_to_dict(t) for t in (await session.execute(stmt)).scalars().all()]
        total = (await session.execute(total_stmt)).scalar() or 0
        return {"items": items, "total": total, "page": page, "pageSize": page_size}

    async def get_tree(self, session: AsyncSession, root_only: bool = False) -> list[dict]:
        stmt = (
            select(Team)
            .where(Team.deleted.is_(False), Team.status == 1)
            .order_by(Team.sort.asc(), Team.created_at.asc())
        )
        teams = [_to_dict(t) for t in (await session.execute(stmt)).scalars().all()]

        def build(parent_id: str) -> list[dict]:
            result = []
            for t in teams:
                if t["parentId"] == parent_id:
                    item = dict(t)
                    item["children"] = [] if root_only else build(t["id"])
                    result.append(item)
            return result

        root_nodes = [t for t in teams if not t["parentId"] or t["parentId"] == "0"]
        if root_only:
            return root_nodes
        return [
            {**t, "children": build(t["id"])} for t in root_nodes
        ]

    async def add_members(
        self, session: AsyncSession, team_id: str, user_ids: list[str]
    ) -> bool:
        await self._find_by_id_or_throw(session, team_id)
        users_stmt = select(User).where(User.id.in_(user_ids), User.deleted.is_(False))
        users = (await session.execute(users_stmt)).scalars().all()
        if len(users) != len(user_ids):
            raise HTTPException(404, "部分用户不存在")

        for user_id in user_ids:
            exists_stmt = select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
            if (await session.execute(exists_stmt)).scalars().first():
                continue
            session.add(
                TeamMember(
                    id=next_snowflake_id(),
                    team_id=team_id,
                    user_id=user_id,
                    member_role="member",
                )
            )
        await session.flush()
        return True

    async def remove_members(
        self, session: AsyncSession, team_id: str, user_ids: list[str]
    ) -> bool:
        await self._find_by_id_or_throw(session, team_id)
        await session.execute(
            sa_delete(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id.in_(user_ids)
            )
        )
        return True

    async def list_members(self, session: AsyncSession, team_id: str) -> list[dict]:
        await self._find_by_id_or_throw(session, team_id)
        stmt = (
            select(TeamMember.user_id, TeamMember.member_role, User.username, User.real_name)
            .join(User, User.id == TeamMember.user_id)
            .where(TeamMember.team_id == team_id, User.deleted.is_(False))
        )
        rows = (await session.execute(stmt)).all()
        return [
            {
                "userId": row.user_id,
                "memberRole": row.member_role,
                "username": row.username,
                "realName": row.real_name,
            }
            for row in rows
        ]

    async def list_mine(self, session: AsyncSession, user_id: str) -> list[dict]:
        """当前用户所在团队（含担任负责人的），登录即可。"""
        ids = await self.list_accessible_team_ids(session, user_id)
        if not ids:
            return []
        stmt = (
            select(Team)
            .where(Team.id.in_(ids), Team.deleted.is_(False))
            .order_by(Team.sort.asc(), Team.created_at.asc())
        )
        return [_to_dict(t) for t in (await session.execute(stmt)).scalars().all()]

    async def list_accessible_team_ids(
        self, session: AsyncSession, user_id: str
    ) -> list[str]:
        """当前用户可见的团队：成员表 ∪ 担任 leader（两者都过滤软删团队）。"""
        stmt = (
            select(TeamMember.team_id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(
                TeamMember.user_id == user_id,
                Team.deleted.is_(False),
                Team.status == 1,
            )
        )
        member_ids = set((await session.execute(stmt)).scalars().all())

        leader_stmt = (
            select(Team.id)
            .where(Team.leader_id == user_id, Team.deleted.is_(False), Team.status == 1)
        )
        leader_ids = set((await session.execute(leader_stmt)).scalars().all())

        return sorted(member_ids | leader_ids)

    async def _find_by_id_or_throw(self, session: AsyncSession, team_id: str) -> Team:
        stmt = select(Team).where(Team.id == team_id, Team.deleted.is_(False))
        team = (await session.execute(stmt)).scalars().first()
        if not team:
            raise HTTPException(404, "团队不存在")
        return team
