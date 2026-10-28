     H DFTACTGRP(*NO)
     FPRICES    IF   E           K DISK
     D Audit           PR                  EXTPGM('AUDITPGM')
     D  pItem                        15A
     C     *ENTRY        PLIST
     C                   PARM                    pItem            15
     C                   PARM                    pPrice            9 2
     C     pItem         CHAIN     PRICES
     C                   IF        %FOUND(PRICES)
     C                   EVAL      pPrice = PRPRICE
     C                   CALLP     Audit(pItem)
     C                   ENDIF
     C                   CALL      'QCMDEXC'
     C                   EXSR      CLEANUP
     C                   SETON                                        LR
     C     CLEANUP       BEGSR
     C                   ENDSR
