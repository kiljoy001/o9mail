</$objtype/mkfile

BIN=/usr/$user/bin/$objtype

TARG=\
	smtpd\
	smtpout\
	mailfs\
	o9dkim\
	o9admin\
	o9mail\

all:V: $TARG

smtpd: smtpd.$O
	$LD -o $target $prereq /usr/scott/lib/o9/libo9.a /amd64/lib/libndb.a

smtpout: smtpout.$O
	$LD -o $target $prereq /usr/scott/lib/o9/libo9.a /amd64/lib/libndb.a

mailfs: mailfs.$O
	$LD -o $target $prereq /usr/scott/lib/o9/libo9.a /amd64/lib/libndb.a

o9dkim: o9dkim.$O
	$LD -o $target $prereq /usr/scott/lib/o9/libo9.a /amd64/lib/libndb.a

o9admin: o9admin.$O
	$LD -o $target $prereq /usr/scott/lib/o9/libo9.a /amd64/lib/libdraw.a /amd64/lib/libmemdraw.a /amd64/lib/libmemlayer.a /amd64/lib/libsec.a /amd64/lib/libmp.a /amd64/lib/libndb.a

o9mail: o9mail.$O
	$LD -o $target $prereq /usr/scott/lib/o9/libo9.a /amd64/lib/libdraw.a /amd64/lib/libmemdraw.a /amd64/lib/libmemlayer.a /amd64/lib/libsec.a /amd64/lib/libmp.a /amd64/lib/libndb.a

install:V: all
	cp $TARG $BIN/

clean:V:
	rm -f *.[$OS] $TARG
