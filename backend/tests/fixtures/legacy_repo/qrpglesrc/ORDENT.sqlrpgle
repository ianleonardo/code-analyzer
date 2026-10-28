**FREE
ctl-opt dftactgrp(*no) actgrp(*caller);

dcl-f ORDDSPF workstn sfile(ORDSFL:rrn);
dcl-f ORDHDR usage(*input:*output:*update) keyed;
dcl-f CUSTMASL1 keyed;

/copy qrpglesrc,ordprotos

dcl-pr PriceCalc extpgm('PRICECALC');
  item char(15);
  price packed(9:2);
end-pr;

dcl-pi *n;
  pOrderNo char(10);
  pMode char(1);
end-pi;

dcl-s rrn packed(4:0);

exsr loadOrder;
dow not *in03;
  exfmt ORDCTL;
  if *in05;
    PriceCalc(item: price);
  elseif *in06;
    callp LogEvent('REFRESH');
  endif;
enddo;
*inlr = *on;

begsr loadOrder;
  chain (pOrderNo) ORDHDR;
  if %found(ORDHDR);
    chain ORDCUST CUSTMASL1;
  endif;
  exec sql select count(*) into :cnt from ORDLINE where ORDNO = :pOrderNo;
  write ORDHDRR;
endsr;

dcl-proc LogEvent;
  dcl-pi *n;
    msg char(50) const;
  end-pi;
  exec sql insert into EVENTLOG (MSG) values(:msg);
end-proc;
