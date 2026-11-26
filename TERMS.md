# Terms of Use (DRAFT, for review)

Terms version: 2026-11-25-v1
Status: DRAFT, for review. Not legal advice.

## Plain-Language Summary

1. Hercyna is an experimental research benchmark. Results are provided as is, with no warranty of any kind.
2. Results describe a fixed protocol, fixed synthetic cases and fixed deciders on a given date. They are not a certification, endorsement, ranking of fitness for any use, or a claim about any product in production.
3. The person using or citing the results is solely responsible for their interpretation and any decision made from them.
4. No liability for any loss arising from use of the software, data, site or results, to the extent the law allows.
5. Product names are trademarks of their owners, used only to identify what was tested. No affiliation or endorsement is implied.
6. Vendors may request a correction or a rerun. See the corrections process at [corrections.html](corrections.html) or open a correction request using the [correction issue template](.github/ISSUE_TEMPLATE/correction_request.md).

## 1. Experimental Scope and Purpose

Hercyna publishes synthetic decision-scored evaluations under frozen benchmark protocols (`protocol/v1.json` for single-session account review and `protocol/v2.json` for multi-session chat). Every number on the site traces to released text-free metric files generated on recorded model snapshots and pinned product versions under fixed view token budgets.

Nothing in this repository or on the static results site constitutes legal advice, security certification, fitness-for-purpose assurance, procurement advice, or a prediction of production behavior.

## 2. Software Disclaimer (Apache-2.0 Sections 7 and 8)

Software in this repository is licensed under the Apache License, Version 2.0 (`LICENSE`). Pursuant to Sections 7 and 8 of Apache-2.0:

- **Disclaimer of Warranty (Section 7):** Unless required by applicable law or agreed to in writing, Licensor provides the Work (and each Contributor provides its Contributions) on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied, including, without limitation, any warranties or conditions of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. You are solely responsible for determining the appropriateness of using or redistributing the Work and assume any risks associated with Your exercise of permissions under the License.
- **Limitation of Liability (Section 8):** In no event and under no legal theory, whether in tort (including negligence), contract, or otherwise, unless required by applicable law (such as deliberate and grossly negligent acts) or agreed to in writing, shall any Contributor be liable to You for damages, including any direct, indirect, special, incidental, or consequential damages of any character arising as a result of this License or out of the use or inability to use the Work (including but not limited to damages for loss of goodwill, work stoppage, computer failure or malfunction, or any and all other commercial damages or losses), even if such Contributor has been advised of the possibility of such damages.

## 3. Data and Results Disclaimer (CC BY 4.0 Section 5)

Benchmark cases, summaries, verification bundles, and released result tables are licensed under the Creative Commons Attribution 4.0 International License (`LICENSE-DATA`). Pursuant to Section 5 of CC BY 4.0:

- The Licensed Material is offered as-is and as-available, with no representations or warranties of any kind concerning the Licensed Material, whether express, implied, statutory, or otherwise, including title, merchantability, fitness for a particular purpose, non-infringement, absence of latent or other defects, accuracy, or the presence or absence of errors.
- To the extent possible, the Licensor is not liable to You on any legal theory (including, without limitation, negligence) or otherwise for any direct, special, indirect, incidental, punitive, consequential, exemplary, or other losses, costs, expenses, or damages arising out of this Public License or use of the Licensed Material.

## 4. Trademarks and Brand Use

Product names, model names, and organization marks referenced on the site (`Mem0`, `LangMem`, `Graphiti`, `FalkorDB`, `Anthropic`, `OpenCode`, `DeepSeek`, `MiniMax`, `Qwen`, `GLM`, and others) remain the trademarks of their respective owners. Inclusion in this benchmark does not imply sponsorship, affiliation, or endorsement. Any embeddable verification badge (`badge/<product>.svg`) identifies participation under a specific frozen protocol only and never certifies safety, accuracy, or superiority.

## 5. Corrections and Right of Reply

Vendors, authors, or maintainers who identify a factual error, packaging defect, or version mismatch may request a correction or rerun via the public [corrections process](corrections.html) or `.github/ISSUE_TEMPLATE/correction_request.md`. All confirmed changes are logged publicly with the exact protocol hash and date.

## 6. Changes to These Terms

Material changes update `Terms version:` at the top of `TERMS.md`. The CLI records only the accepted version identifier and calendar date in `~/.config/hercyna/accepted.json` and asks for fresh acceptance when the version changes.
