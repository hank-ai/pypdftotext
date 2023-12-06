# pypdftotext
**_A structured text extraction extension for pypdf_**...

Returns the text of a pdf in "layout mode". Structural fidelity is on par with SOTA in open source python tools, namely `pdftotext.PDF([pdf io obj], physical=True)`, with no copyleft GPL strings attached.

## Requirements
- Python 3.10+
- pypdf 3.16+

## Installation

Currently in pre-release. The beta version can be installed from test.pypi.org using the following commands.

```cmd
pip install pypdf
pip install -i https://test.pypi.org/simple/ pypdftotext
```

## Usage

### Get Text from All PDF Pages in a Single String
```python3
from pathlib import Path
import pypdftotext
pdf = Path("some_pdf.pdf")  # can be bytes, Path, PdfReader, or io.BytesIO; used Path for convenience
pdf_text = pypdftotext.pdf_text(pdf)
print(pdf_text)
```

### Other Top Level Functions
 - `pypdftotext.pdf_text_pages()` : returns a list of strings, one per pdf page
 - `pypdftotext.extract_structured_text()` : returns the text of a single page when passed a pypdf PageObject

## Performance Comparisons
<details>
  <summary>Example 1</summary>

### Source file: 
[Claim Maker Alerts Guide_pg2.PDF](https://github.com/hank-ai/pypdftotext/files/13591612/Claim.Maker.Alerts.Guide_pg2.PDF)


### BEFORE - pypdf PageObject.extract_text() output:
```text
 Updated System Responses for Common Scenarios 
 Scenario  Before Change  After  Why? 
 An On Hold / Missing 
 Documents case receives its 
 first documentation set after 
 coding operations have 
 already begun for the batch 
 (batch state = In Progress).  New doc info was 
 logged but no 
 further automated 
 action was taken.  Leave state as On 
 Hold and update state 
 reason to Ready To 
 Code.  Batches can be released early 
 and coders can code all they can 
 and then leave the batch in In 
 Progress. When docs come in, 
 the case is picked up by the 
 normal On Hold process due to 
 the assignment of the Ready to 
 Code state reason. 
 An “incomplete” case (not 
 Code Completed or Ignored) 
 in an “in flight” batch (state = 
 Reconciled, Assigned, or In 
 Progress) receives new 
 documents.  All documents 
 were “overwritten” 
 with data from the 
 new documents.  All manually attached 
 PDFs are preserved 
 in place and all 
 “extracted” 
 documents are 
 aggregated under a 
 SUPERSEDED ON 
 [DATE] text doc with 
 type Complete 
 Record.  Ensures that ALL info that has 
 arrived for the case remains 
 visible to users. Specifically 
 addresses split labor / C-section 
 cases, allowing a coder to refer 
 back to the “Superseded” 
 documents to make sure a newly 
 extracted “C-section only” 
 document wasn’t really a Labor 
 to C-section case. 
 New documents are received 
 for a Code Completed or 
 Ignored case in an “in flight” 
 batch.  New doc info was 
 logged but no 
 further automated 
 action was taken.  Existing documents 
 are “superseded” 
 (see previous) and 
 the case is set back 
 to On Hold / Ready to 
 Code.  Prompts the coder to review the 
 new documentation set while 
 retaining all previously applied 
 codes.  If no significant change is 
 noted, the case can simply be set 
 back to Code Completed. 
 Documentation for an 
 “uncoded” (aka not Code 
 Completed) case or a new 
 patient is received for a 
 Complete or Charges Entered 
 batch.  New case info 
 was logged but 
 no further 
 automated action 
 was taken.  The case is added to 
 a new batch with the 
 same date of service. 
 Set state to Ignored 
 on the original case (if 
 it exists) and add 
 notes to both the 
 original and new 
 cases indicating the 
 link between the two.  Ensures proper review of any 
 additional documentation 
 received for a previously 
 completed batch as well as 
 documentation for brand new 
 cases after a batch has already 
 been Completed. Notes on the 
 original and duplicate case 
 ensure that users are aware of 
 actions taken by the system. 
 Documentation for a Code 
 Completed case in a 
 Complete or Charges Entered 
 batch is received.  New doc info was 
 logged but no 
 further automated 
 action was taken.  Existing case 
 documents are left in 
 place and the new 
 documentation is 
 added as a PDF 
 attachment with type 
 “complete record” and 
 title POSTED LATE - 
 [DATE].  The status of the new document 
 is clearly indicated as arriving 
 AFTER the associated case was 
 coded avoiding potential 
 confusion regarding which 
 documentation was utilized at the 
 time of coding while also 
 providing access to the new info 
 and allowing the end user to 
 determine the correct course of 
 action. 
```

### AFTER - pypdftotext.get_text() output:

```text
 Updated System Responses for Common Scenarios


  Scenario                                 Before Change             After                           Why?

  An On Hold / Missing                     New doc info was          Leave state as On               Batches can be released early
  Documents case receives its              logged but no             Hold and update state           and coders can code all they can
  first documentation set after            further automated         reason to Ready To              and then leave the batch in In
  coding operations have                   action was taken.         Code.                           Progress. When docs come in,
  already begun for the batch                                                                        the case is picked up by the
  (batch state = In Progress).                                                                       normal On Hold process due to
                                                                                                     the assignment of the Ready to
                                                                                                     Code state reason.

  An “incomplete” case (not                All documents             All manually attached           Ensures that ALL info that has
  Code Completed or Ignored)               were “overwritten”        PDFs are preserved              arrived for the case remains
  in an “in flight” batch (state =         with data from the        in place and all                visible to users. Specifically
  Reconciled, Assigned, or In              new documents.            “extracted”                     addresses split labor / C-section
  Progress) receives new                                             documents are                   cases, allowing a coder to refer
  documents.                                                         aggregated under a              back to the “Superseded”
                                                                     SUPERSEDED ON                   documents to make sure a newly
                                                                     [DATE] text doc with            extracted “C-section only”
                                                                     type Complete                   document wasn’t really a Labor
                                                                     Record.                         to C-section case.

  New documents are received               New doc info was          Existing documents              Prompts the coder to review the
  for a Code Completed or                  logged but no             are “superseded”                new documentation set while
  Ignored case in an “in flight”           further automated         (see previous) and              retaining all previously applied
  batch.                                   action was taken.         the case is set back            codes.  If no significant change is
                                                                     to On Hold / Ready to           noted, the case can simply be set
                                                                     Code.                           back to Code Completed.

  Documentation for an                     New case info             The case is added to            Ensures proper review of any
  “uncoded” (aka not Code                  was logged but            a new batch with the            additional documentation
  Completed) case or a new                 no further                same date of service.           received for a previously
  patient is received for a                automated action          Set state to Ignored            completed batch as well as
  Complete or Charges Entered              was taken.                on the original case (if        documentation for brand new
  batch.                                                             it exists) and add              cases after a batch has already
                                                                     notes to both the               been Completed. Notes on the
                                                                     original and new                original and duplicate case
                                                                     cases indicating the            ensure that users are aware of
                                                                     link between the two.           actions taken by the system.

  Documentation for a Code                 New doc info was          Existing case                   The status of the new document
  Completed case in a                      logged but no             documents are left in           is clearly indicated as arriving
  Complete or Charges Entered              further automated         place and the new               AFTER the associated case was
  batch is received.                       action was taken.         documentation is                coded avoiding potential
                                                                     added as a PDF                  confusion regarding which
                                                                     attachment with type            documentation was utilized at the
                                                                     “complete record” and           time of coding while also
                                                                     title POSTED LATE -             providing access to the new info
                                                                     [DATE].                         and allowing the end user to
                                                                                                     determine the correct course of
                                                                                                     action.
```

</details>

<details>
  <summary>Example 2</summary>

### Source file: 
[Epic Page.PDF](https://github.com/hank-ai/pypdftotext/files/13591605/Epic.Page.PDF)


### BEFORE - pypdf PageObject.extract_text() output:
```text
 
All Postprocedure Notes 
Anesthesia Post Evaluation
Procedure Summary  
Date: 10/11/23 Room / Location: EHMC ENDOSCOPY
Anesthesia Start: 0852 Anesthesia Stop: 0918
Procedure: COLONOSCOPY Diagnosis: Cancer screening
Scheduled Providers: Walter A Klein, MD; Danny Chaung, 
DOResponsible Provider: Danny Chaung, DO
Anesthesia Type: general ASA Status: 2
Patient location during evaluation: PACU
Post op Vital Signs: stable
Level of consciousness: awake and alert
Pain management: adequate analgesia
Airway patency: patent
Anesthetic complications: no
Respiratory status: unassisted
Hydration status: continuing
Post-op Complications: No
Assessment: Nausea and Vomiting: absent
MIPS Measure #404 - Smoking Abstinence
Is the patient a current smoker? No (XX404)  
 
 Last edited 10/11/23 0919 by Danny Chaung, DO
Date of Service 10/11/23 0918
Status: Signed 
```

### AFTER - pypdftotext.get_text() output:
```text
All Postprocedure Notes
   Last edited 10/11/23 0919 by Danny Chaung, DO
   Date of Service 10/11/23 0918
   Status: Signed
Anesthesia Post Evaluation

Procedure Summary

   Date: 10/11/23                                                Room / Location: EHMC ENDOSCOPY
   Anesthesia Start: 0852                                        Anesthesia Stop: 0918
   Procedure: COLONOSCOPY                                        Diagnosis: Cancer screening
   Scheduled Providers: Walter A Klein, MD; Danny Chaung,        Responsible Provider: Danny Chaung, DO
   DO
   Anesthesia Type: general                                      ASA Status: 2


Patient location during evaluation: PACU
Post op Vital Signs: stable

Level of consciousness: awake and alert
Pain management: adequate analgesia
Airway patency: patent
Anesthetic complications: no
Respiratory status: unassisted
Hydration status: continuing
Post-op Complications: No



Assessment: Nausea and Vomiting: absent




MIPS Measure #404 - Smoking Abstinence
Is the patient a current smoker? No (XX404)



```

</details>


