       IDENTIFICATION DIVISION.
       PROGRAM-ID. CUSTBAT.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT CUST-IN  ASSIGN TO CUSTIN
               ORGANIZATION IS SEQUENTIAL
               FILE STATUS IS WS-FS1.
           SELECT CUST-MST ASSIGN TO CUSTMST
               ORGANIZATION IS INDEXED
               ACCESS MODE IS DYNAMIC
               RECORD KEY IS CM-ID.
           SELECT RPT-OUT  ASSIGN TO UT-S-RPTOUT.
       DATA DIVISION.
       FILE SECTION.
       FD  CUST-IN.
       01  CI-REC.
           05  CI-ID            PIC X(10).
           05  CI-AMT           PIC S9(7)V99 COMP-3.
       FD  CUST-MST.
       01  CM-REC.
           05  CM-ID            PIC X(10).
           05  CM-BAL           PIC S9(9)V99 COMP-3.
       FD  RPT-OUT.
       01  RPT-LINE             PIC X(132).
       WORKING-STORAGE SECTION.
       01  WS-FS1               PIC XX.
       01  WS-CALC-PGM          PIC X(8).
       01  WS-EOF               PIC X VALUE 'N'.
           88 END-OF-FILE       VALUE 'Y'.
       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT CUST-IN
                I-O   CUST-MST
                OUTPUT RPT-OUT.
           PERFORM READ-PARA.
           PERFORM PROCESS-PARA UNTIL END-OF-FILE.
           CLOSE CUST-IN CUST-MST RPT-OUT.
           STOP RUN.
       READ-PARA.
           READ CUST-IN AT END SET END-OF-FILE TO TRUE.
       PROCESS-PARA.
           MOVE CI-ID TO CM-ID.
           READ CUST-MST INVALID KEY DISPLAY 'NOT FOUND'.
           EVALUATE TRUE
              WHEN CI-AMT > 0
                 MOVE 'INTCALC' TO WS-CALC-PGM
              WHEN OTHER
                 MOVE 'FEECALC' TO WS-CALC-PGM
           END-EVALUATE.
           CALL WS-CALC-PGM USING CM-REC CI-REC.
           CALL 'AUDITLOG' USING CM-REC.
           REWRITE CM-REC.
           WRITE RPT-LINE FROM CM-REC.
           EXEC SQL
              UPDATE ACCT_SUMMARY SET LAST_RUN = CURRENT DATE
              WHERE ACCT_ID = :CM-ID
           END-EXEC.
           PERFORM READ-PARA.
