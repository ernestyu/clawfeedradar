# -*- coding: utf-8 -*-
"""clawfeedradar package.

v0 目标：
- 从配置和 SQLite 知识库中构建兴趣簇视图；
- 接收一批 Candidate（可先用假数据），跑打分 + 多样性 + 探索逻辑；
- 输出 JSON（后续再接真实源、RSS 和 LLM）。
"""
