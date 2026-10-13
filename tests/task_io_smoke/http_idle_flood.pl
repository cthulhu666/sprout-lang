#!/usr/bin/env perl
# Client for http_idle_flood.spr: one request, then empty lines as fast as the socket takes them, so
# every read the server makes finds data. Prints "closed <s>" when the server closes, in whole seconds
# from the response, or "open <s>" if it is still open after 3 s. Whole seconds because the Linux
# smoke image ships perl-base, which has no Time::HiRes. A separate process, because a client
# task in the server's own process only runs when the server parks, so it cannot keep the socket
# readable.
use strict;
use warnings;
use IO::Socket::INET;

my $port = shift or die "usage: $0 <port>\n";
my $s = IO::Socket::INET->new(PeerAddr => "127.0.0.1:$port") or die "connect: $!\n";
print $s "GET / HTTP/1.1\r\nHost: x\r\n\r\n";
my $reply = '';
sysread($s, $reply, 4096, length $reply) until $reply =~ /hello/;
$SIG{PIPE} = 'IGNORE';
$s->blocking(0);
my $start = time;
my $lines = "\r\n" x 65536;
my $closed = 0;
while (time - $start < 3) {
    my $w = syswrite($s, $lines);
    if (!defined $w && !$!{EAGAIN}) { $closed = 1; last }
    my $r = sysread($s, my $junk, 4096);
    if (defined $r && $r == 0) { $closed = 1; last }
    if (!defined $r && !$!{EAGAIN}) { $closed = 1; last }
}
printf "%s %d\n", ($closed ? "closed" : "open"), time - $start;
