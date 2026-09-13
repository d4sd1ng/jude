from __future__ import annotations

import asyncio

from fastapi import APIRouter

from web.app import coding

router = APIRouter()


@router.get("/api/coding/repositories")
def coding_repositories(): return coding.repositories()


@router.post("/api/coding/status")
def coding_status(payload: dict): return {"status": coding.status(str(payload["repo"]))}


@router.post("/api/coding/test")
async def coding_test(payload: dict): return await asyncio.to_thread(coding.test, str(payload["repo"]))


@router.post("/api/coding/read")
def coding_read(payload: dict): return {"content": coding.read(str(payload["path"]))}


@router.post("/api/coding/write")
def coding_write(payload: dict): return {"path": coding.write(str(payload["path"]), str(payload["content"]))}


@router.post("/api/coding/diff")
def coding_diff(payload: dict): return {"diff": coding.diff(str(payload["repo"]))}


@router.post("/api/coding/branch")
def coding_branch(payload: dict): return {"branch": coding.create_branch(str(payload["repo"]), str(payload["branch"]))}


@router.post("/api/coding/commit")
def coding_commit(payload: dict): return {"commit": coding.commit(str(payload["repo"]), str(payload["message"]), list(payload["paths"]))}


@router.post("/api/coding/push")
def coding_push(payload: dict): return {"remote": coding.push(str(payload["repo"]), str(payload["branch"]))}


@router.post("/api/coding/pr")
def coding_pr(payload: dict): return {"url": coding.create_pr(str(payload["repo"]), str(payload["title"]), str(payload["body"]), bool(payload.get("draft", True)))}
