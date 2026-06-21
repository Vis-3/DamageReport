# The Damage Report: Data Science Extension

A machine learning layer built on top of the [DE pipeline](../README.md), answering four questions the SQL marts couldn't: **is the frequency trend statistically significant, which years were structurally anomalous, can we predict whether a storm will kill someone, and how bad will it be?**

---

## DS Workflow

```
BigQuery mart tables
        │
        ▼
00_eda.ipynb                  Understand the data before modelling
        │                     target distribution · fatality rates · skewness · correlation
        ▼
01_trend_decomposition.ipynb  Statistical significance of observed trends
        │                     Mann-Kendall test · Sen's slope · frequency vs severity
        ▼
02_anomaly_detection.ipynb    Flag structurally unusual years
        │                     Isolation Forest · log transform · multi-dimensional scoring
        ▼
03_fatality_prediction.ipynb  Binary classification — will this event cause deaths?
        │                     feature engineering · class imbalance · threshold optimisation
        │                     cost-sensitive learning · SHAP · model serialisation
        ▼
04_advanced_prediction.ipynb  How bad? Damage tier + hierarchical fatality severity
                              storm intensity · Census population density · two-stage pipeline
                              HistGradientBoosting · NaN-native · CRITICAL recall optimisation
```

---

## The Story

### Act 1: What Does the Data Look Like Before We Model It?

Before writing a single model, we ran EDA to understand the target variable and feature distributions. The findings shaped every modelling decision that followed.

**The target is severely imbalanced.** 99.40% of 1.72M events are non-fatal. Fatal events follow a power-law distribution most kill 1-5 people, events killing 100+ are extremely rare. This ruled out regression immediately: binary classification is the correct framing.

**The most important finding:** fatality rate and event frequency are inversely related. HAIL has 337K events and a 0.00% fatality rate. MARINE has 14K events and a 9.03% fatality rate. The storms that dominate the dataset by count are the least deadly per occurrence. A model that learns from raw counts will systematically underweight the events that matter most.

**Damage is not correlated with deaths (r=0.03).** Economic cost and human cost are independent dimensions an expensive storm is not necessarily a deadly one. Heat waves kill with minimal property damage. Hurricanes do both.

**Damage skewness is 323.2 raw, 0.9 after log transform.** Log transformation is not optional for distance-based algorithms on this data.

---

### Act 2: Is the Frequency Trend Real, or Just Noise?

We observed rising event counts throughout the DE pipeline, but eyeballing a noisy time series is not analysis. Mann-Kendall non-parametric trend test gives a definitive answer.

**Frequency: statistically significant increasing trend.**
- p < 0.0001 - essentially zero probability this is random noise
- Tau = +0.655 - strong monotonic relationship
- Sen's slope: +813 events/year the median annual increase over 30 years

**Severity per event: no significant trend.**
- p = 0.134 - above the 0.05 threshold, cannot reject null hypothesis
- Tau = -0.195 - weakly negative, not significant
- The 2005 Katrina spike is an outlier, not a trend

*"Storm frequency shows a statistically significant upward trend at p<0.0001. Severity per event shows no trend (p=0.13). We are reporting more storms but individual storms are not becoming more destructive on average."*

Mann-Kendall was chosen over linear regression because it makes no normality assumption and is robust to outliers. A single Katrina year shifts OLS slope significantly but barely moves the median (Sen's slope).

---

### Act 3: Which Years Were Structurally Unusual?

Three anomalous years flagged by Isolation Forest on three features: event count, average damage per event, total deaths (log-scaled, StandardScaler applied).

**2005 score: -0.670 (most anomalous)**
Hurricane Katrina. Average damage per event $3M - 6x the dataset mean. The most economically anomalous year by a wide margin.

**2011 score: -0.563**
Joplin tornado season. Peak event count in the dataset (79,091) AND high deaths (1,096). Anomalous on two dimensions simultaneously frequency and lethality.

**2025 score: -0.582**
Anomalous in the opposite direction. Second-highest event count but lowest average damage per event ($64K). NOAA damage reports for recent events are still being filed a data completeness artifact, not a real-world disaster. The model correctly flagged structural unusualness; interpreting *why* requires domain knowledge.

Key lesson: anomaly detection flags deviation from the norm in any direction. 2025 is as statistically unusual as 2005, for completely different reasons.

Normal years cluster tightly in anomaly score range [-0.40, -0.55].

---

### Act 4: Can We Predict Whether a Storm Will Kill Someone?

**Problem framing:** binary classification - will this event cause direct fatalities?
**Dataset:** 1,720,900 events | 10,377 fatal (0.60% positive rate)
**Split:** temporal train 1996-2010, test 2011-2025 (no data leakage)
**Primary metric:** PR-AUC (precision-recall area under curve) - appropriate for severe class imbalance where accuracy is misleading

**Features:** event_type_group (one-hot), region (one-hot), decade, event_month, plus lag features: prior-year state event count, deaths, damage.

**Lag features** were engineered because a state's prior-year storm history predicts current fatality risk — regions under sustained storm stress show elevated risk the following year. SHAP confirmed `lag_deaths` carries genuine marginal predictive value; `lag_event_count` and `lag_damage` appeared important by built-in tree importance but were not confirmed by SHAP, illustrating the known overcounting bias of impurity-based importance for continuous features.

**Baseline results:**

| Model | PR-AUC | ROC-AUC | Recall@threshold |
|---|---|---|---|
| Logistic Regression + lag | 0.135 | 0.845 | 0.462 |
| Gradient Boosting + lag | 0.064 | 0.853 | 0.511 |

Both models achieve ROC-AUC ~0.85 significantly above random (0.50). The low PR-AUC reflects severe class imbalance, not poor discriminative power.

**Threshold optimisation:** default 0.50 threshold is inappropriate for safety-critical applications. At threshold=0.02, GB achieves recall=0.511. At threshold=0.82, LR achieves recall=0.462. The model is flagging more events as potentially fatal, accepting more false positives to catch more true positives the correct tradeoff when missing a deadly event costs far more than a false alarm.

**Feature importance (SHAP):**
- HEAT events: strong positive signal (+2 to +4 SHAP value). Heat waves kill disproportionately relative to economic damage.
- HAIL events: strong negative signal. Being a hail event decreases fatality prediction - more importantly, *not being hail* is a proxy for something more dangerous.
- lag_deaths: prior year state fatality count increases current risk prediction.
- region_WEST: elevated risk consistent with wildfire patterns.

---

### Act 5: Can Domain Knowledge Beat Generic Modelling?

EDA revealed that MARINE events kill at 9.03% rate while HAIL events kill at 0.00%. Generic models treat these equally in the loss function. We tested four methods of encoding domain knowledge:

**Method 1: Instance-level sample weights (event-type danger multipliers)**
Fatal MARINE events penalised 5x more than fatal HAIL events during training. GB PR-AUC jumped from 0.064 to **0.174** the largest gain across all experiments. Domain knowledge as learning bias outperformed feature engineering.

**Method 2: Probability calibration (isotonic regression)**
Raw GB probabilities are compressed near zero due to class imbalance. When the model says 0.8 probability, only ~5% of those events are actually fatal. Calibration aligned probabilities with real frequencies (PR-AUC 0.168). Use the calibrated model when communicating probabilities to stakeholders.

**Method 3: XGBoost with scale_pos_weight**
Industry-standard approach. `scale_pos_weight=166` tells XGBoost the positive class is 166x rarer than negative. PR-AUC=0.170, ROC-AUC=0.856. Attempted custom focal loss implementation gradient was numerically unstable. Built-in library defaults outperformed the custom implementation, which is the common real-world outcome.

**Method 4: Targeted SMOTE**
SMOTE applied only to dangerous event types (MARINE, HEAT, HURRICANE, TORNADO, FIRE). Sampling strategy=0.1 to avoid distribution distortion as synthetic data inflates fatality rates and breaks calibration if overused. Result: PR-AUC=0.165, but highest recall of any model at **0.522**.

**Ensemble testing:** four blending strategies tested. Four-way ensemble marginally edged the best individual model (PR-AUC 0.1743 vs 0.1741) negligible gain. Models share the same feature set and architecture, so their errors are correlated. Ensemble gains require model diversity.

**Final model selection by use case:**

| Use case | Model | PR-AUC | Recall |
|---|---|---|---|
| Analyst risk dashboard | Weighted GB | 0.1741 | 0.387 |
| Real-time alert system | Targeted SMOTE GB | 0.1652 | 0.522 |
| Stakeholder reporting | Calibrated GB | 0.1677 | — |
| Production baseline | XGBoost scale_pos | 0.1702 | — |

No single model dominates all metrics. Deployment context determines the right choice.

---

### Act 6: How Bad Will It Be? Damage Tiers and Fatality Severity

Notebook 03 answered *fatal or not*. This notebook answers *how bad* adding storm intensity (F-scale, magnitude) and Census 2020 population density from a new ingestion pipeline, then predicting across two dimensions: economic damage tier and human fatality severity.

**Damage tier classification (4-class):**

| Tier | Threshold | Macro F1 |
|---|---|---|
| LOW | < $10K | — |
| MEDIUM | $10K – $1M | — |
| HIGH | $1M – $100M | — |
| CATASTROPHIC | > $100M | — |
| **Overall** | | **0.28** |

Thresholds are domain-defined (FEMA-aligned), not quantile-based. `fscale_num` and `decade` are the top SHAP features, confirming that tornado intensity and the long-term trend in storm severity drive damage tier.

**Hierarchical fatality severity pipeline:**

MASS events (10+ deaths) total 148 across 30 years — too rare for supervised learning. The solution: decompose into two binary problems.

```
10,000 storm events
    │
    ▼ Stage 1: NONE vs ANY_DEATH   PR-AUC 0.154
   88 flagged  (99.1% discarded without analyst review)
    │
    ▼ Stage 2: MINOR vs CRITICAL   PR-AUC 0.061
   12 CRITICAL alerts prioritised  (3+ deaths, multi-agency response)
   76 MINOR events reviewed second
```

**Key technical decision — NaN ≠ zero for magnitude:** Filling missing magnitude with 0 told the model a heat wave had zero wind speed. `HistGradientBoostingClassifier` handles NaN via native separate decision paths, improving Stage 1 PR-AUC from 0.138 to 0.154. The fix: never impute a structural absence as a zero measurement.

**SHAP confirms new features carry signal:** magnitude ranks #1 for Stage 1 fatality prediction (SHAP 1.34), population_density ranks #3 the Census ingestion was justified. The same features that drove the most SHAP importance, however, did not improve PR-AUC above notebook 03's 0.174, because magnitude is absent for 48.5% of events (heat, flood, drought - the most dangerous types). The new features enable richer outputs, not a better binary classifier.

---

## Analytical Limitations (Notebooks 01–03)

- **Features are pre-event only.** Damage amount is excluded to avoid data leakage - it's measured at the same time as deaths, not before. A real deployment system would only have event type, location, and timing.
- **Temporal split means test set includes climate-shifted years.** The model trained on 1996-2015 patterns may underfit recent wildfire-driven fatalities in the West.
- **Lag features assume state-level patterns persist year-over-year.** A state that had a catastrophic year followed by policy changes (better warning systems, evacuation routes) would have misleading lag features.
- **0.61% positive rate limits recall ceiling.** With ~11K fatal events in 1.79M, even a perfect model would struggle to push recall above ~0.65 without unacceptable false positive rates at meaningful precision levels.
- **CRITICAL recall of 54% at Stage 2** is achieved on Stage 1 positives only - the end-to-end pipeline catches roughly 20% of all true CRITICAL events (Stage 1 recall × Stage 2 recall).

---

## How to Run

```bash
# From project root
cd DamageReport

# Install dependencies
uv sync

# Launch Jupyter
jupyter notebook notebooks/
```

Run notebooks in order: `00_eda` → `01_trend` → `02_anomaly` → `03_fatality` → `04_advanced_prediction`.

Each notebook connects to BigQuery directly — set your keyfile path in Cell 1.

Serialised models are saved to `models/` after running notebook 03.

---

