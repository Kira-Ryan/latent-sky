# latent-sky.dev

Registered at **Cloudflare Registrar** on Kira's personal account (kiraryan27@gmail.com),
25 Aug 2026, after AWS Route 53 refused the registration twice — a sub-second internal
failure ("contact AWS Support"), the same new-account risk screen that denied the GPU
quota. Nothing was charged; the domain never reached a registry via AWS.

Holding the name at an independent registrar is the better arrangement anyway: hosting
can move, the name stays.

## How it is wired

| Piece | Value |
|---|---|
| Registrar / DNS | Cloudflare (personal account), zone `c2ba55da1e8e82647f26b21b1d524876` |
| Certificate | ACM us-east-1, `latent-sky.dev` + `www`, DNS-validated |
| Distribution | CloudFront `E2F82BMNK777GD` (aliases attached) |
| Origin | private S3 `latentsky-site-438173644568` via OAC |

DNS records — **all four are grey-cloud (DNS-only) and must stay that way**:

    CNAME  latent-sky.dev        -> d7kh11rdqrpy5.cloudfront.net
    CNAME  www                   -> d7kh11rdqrpy5.cloudfront.net
    CNAME  _1c2311a9...          -> ..acm-validations.aws     (keep: cert renewal)
    CNAME  _53c2cfca...          -> ..acm-validations.aws     (keep: cert renewal)

**Never proxy these (orange cloud).** Cloudflare's proxy in front of CloudFront is a
double-CDN — precisely the architecture that was intermittently 522-ing ultisim.com in
August. Grey-cloud is deliberate, not an oversight.

Do not delete the two `_acm-validations` CNAMEs: ACM re-checks them to auto-renew the
certificate. Losing them means the cert silently fails to renew.

## Renewal

Domain auto-renews at Cloudflare (~$10–12/yr) on the personal account. The ACM
certificate renews itself for free while the validation CNAMEs exist.
