"""业务路由汇总：所有模块共用一套资源路由，流水线路由独立注册。"""
from __future__ import annotations

from app.routers import resource, pipeline_routes, moisture_routes

ROUTERS = [moisture_routes, resource, pipeline_routes]