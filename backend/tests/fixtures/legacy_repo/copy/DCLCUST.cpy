      ******************************************************************
      * DCLGEN TABLE(PROD.CUSTOMER)
      ******************************************************************
           EXEC SQL DECLARE PROD.CUSTOMER TABLE
           ( CUST_ID                        CHAR(10) NOT NULL,
             CUST_NAME                      VARCHAR(40) NOT NULL,
             CUST_BAL                       DECIMAL(11, 2)
           ) END-EXEC.
       01  DCLCUSTOMER.
           10 CUST-ID              PIC X(10).
           10 CUST-NAME            PIC X(40).
           10 CUST-BAL             PIC S9(9)V9(2) USAGE COMP-3.
