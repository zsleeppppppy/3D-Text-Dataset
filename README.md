# 3D-Text Dataset

用于探索和构建 **3D-sensitive molecule–text dataset** 的 Pilot 项目。

## Stage 1：原始候选数据采集

第一阶段只负责采集，不提前假设文本真的与 3D 几何相关。每个候选样本尽量包含：

`CID + SMILES + 3D SDF + PubChem description + molecular properties`

后续 Stage 2 再人工/模型辅助判断文本属于：

- **A** — 直接描述 3D 几何、构象、手性或空间关系
- **B** — 描述明显受 3D 影响的 property
- **C** — 描述可能与 3D 有关的功能/活性
- **D** — 与 3D 基本无关

## 目录

```text
3D-Text-Dataset/
├── README.md
├── requirements.txt
├── scripts/
│   └── collect_pubchem.py
└── data/
    ├── cids.txt
    ├── metadata.csv          # 运行后生成
    ├── descriptions/        # 运行后生成
    └── sdf/                 # 运行后生成
```

## 运行

```bash
pip install -r requirements.txt
python scripts/collect_pubchem.py --cids data/cids.txt --out data
```

`data/cids.txt` 先放少量 CID 做 smoke test。确认流程无误后，再扩展到 100–500 个分子的 Pilot Dataset。

> 注意：CID 层面的 `3D + text` 配对只是 **candidate pair**。同一个分子能同时找到 3D 和文本，并不能证明文本真正提供了 3D-sensitive semantic supervision。