num_limit=10;
while true; do 
        echo "----------------------------------------------------------------------------"
        date;
    if [ -f output/egfr.sqlite ] &&
           [ "$(sqlite3 output/egfr.sqlite \
               "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='compound';" \
               2>/dev/null)" = "1" ]; then

sqlite3 -cmd ".mode box --quote off" \
        -cmd ".nullvalue ''" \
        output/egfr.sqlite "
select *
from (
    select iteration as cycle,
           count(*) as count,

           case when avg(rdk_mw) is not null
                then printf('%6.1f', avg(rdk_mw))
           end as mw,

           case when avg(rdk_rotatable_bonds) is not null
                then printf('%4.1f', avg(rdk_rotatable_bonds))
           end as rot,

           nullif(count(docking_score), 0) as \"#glide\",
           case when min(docking_score) is not null
                then printf('%6.2f', min(docking_score))
           end as glide,

           nullif(count(amber_gbsa_score), 0) as \"#amber\",
           case when min(amber_gbsa_score) is not null
                then printf('%6.2f', min(amber_gbsa_score))
           end as amber,

           nullif(count(prime_gbsa_score), 0) as \"#prime\",
           case when min(prime_gbsa_score) is not null
                then printf('%6.2f', min(prime_gbsa_score))
           end as prime,

           strftime('%H:%M', min(modified_at), '+9 hours') as modified
    from compound
    group by iteration
    order by cycle desc
    limit $num_limit
)
order by cycle;
"
	# ssh -n slurm squeue; echo;
        njobs_slurm=$(ssh slurm squeue | grep -v JOBID | wc -l);
        njobs_t41=$(ssh t41 squeue | grep -v JOBID | wc -l);
        printf 'Num_Jobs:  slurm=%s  t41=%s\n\n' "$njobs_slurm" "$njobs_t41";

        ssh plaza licstatus.run;

    break;

    fi

        if IFS= read -r -s -n 1 -t 1 key; then
            case "$key" in
                " ")
                    num_limit=10000;
                    continue;;
                "")
                    num_limit=10000;
                    continue;;
            esac
        else
            num_limit=10;
	    sleep 5s;
        fi

        echo; echo;

done 

