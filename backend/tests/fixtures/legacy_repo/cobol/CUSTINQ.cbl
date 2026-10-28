000100 IDENTIFICATION DIVISION.                                         CUSTINQ 
000200 PROGRAM-ID. CUSTINQ.                                             CUSTINQ 
000300* CICS customer inquiry - pseudo-conversational                   CUSTINQ 
000400 ENVIRONMENT DIVISION.                                            CUSTINQ 
000500 DATA DIVISION.                                                   CUSTINQ 
000600 WORKING-STORAGE SECTION.                                         CUSTINQ 
000700 01  WS-PGM-NAME          PIC X(8)  VALUE 'CUSTUPD'.              CUSTINQ 
000800 01  WS-COMMAREA.                                                 CUSTINQ 
000900     05  WS-CUST-ID       PIC X(10).                              CUSTINQ 
001000     05  WS-STATE         PIC X.                                  CUSTINQ 
001100         88  FIRST-TIME   VALUE ' '.                              CUSTINQ 
001200     COPY CUSTMAP.                                                CUSTINQ 
001300     EXEC SQL INCLUDE SQLCA END-EXEC.                             CUSTINQ 
001400     EXEC SQL INCLUDE DCLCUST END-EXEC.                           CUSTINQ 
001500 LINKAGE SECTION.                                                 CUSTINQ 
001600 01  DFHCOMMAREA          PIC X(11).                              CUSTINQ 
001700 PROCEDURE DIVISION.                                              CUSTINQ 
001800 0000-MAIN.                                                       CUSTINQ 
001900     IF EIBCALEN = 0                                              CUSTINQ 
002000        PERFORM 1000-SEND-MAP                                     CUSTINQ 
002100     ELSE                                                         CUSTINQ 
002200        PERFORM 2000-PROCESS THRU 2000-EXIT                       CUSTINQ 
002300     END-IF.                                                      CUSTINQ 
002400     EXEC CICS RETURN TRANSID('CINQ')                             CUSTINQ 
002500          COMMAREA(WS-COMMAREA) END-EXEC.                         CUSTINQ 
002600 1000-SEND-MAP.                                                   CUSTINQ 
002700     EXEC CICS SEND MAP('CINQMAP') MAPSET('CUSTSET')              CUSTINQ 
002800          ERASE END-EXEC.                                         CUSTINQ 
002900 2000-PROCESS.                                                    CUSTINQ 
003000     EXEC CICS RECEIVE MAP('CINQMAP') MAPSET('CUSTSET')           CUSTINQ 
003100          END-EXEC.                                               CUSTINQ 
003200     EXEC SQL                                                     CUSTINQ 
003300         SELECT CUST_NAME, CUST_BAL INTO :WS-NAME, :WS-BAL        CUSTINQ 
003400           FROM CUSTOMER WHERE CUST_ID = :WS-CUST-ID              CUSTINQ 
003500     END-EXEC.                                                    CUSTINQ 
003600     IF SQLCODE = 0 AND WS-BAL > 1000                             CUSTINQ 
003700        EXEC CICS LINK PROGRAM(WS-PGM-NAME)                       CUSTINQ 
003800             COMMAREA(WS-COMMAREA) END-EXEC                       CUSTINQ 
003900     END-IF.                                                      CUSTINQ 
004000     EXEC CICS READ FILE('CUSTVSM') INTO(WS-REC)                  CUSTINQ 
004100          RIDFLD(WS-CUST-ID) END-EXEC.                            CUSTINQ 
004200     EXEC CICS WRITEQ TS QUEUE('CUSTLOG') FROM(WS-REC) END-EXEC.  CUSTINQ 
004300 2000-EXIT.                                                       CUSTINQ 
004400     EXIT.                                                        CUSTINQ 
